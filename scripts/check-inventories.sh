#!/usr/bin/env bash
# Syntax-check every playbook AND every inventory.
#
# Two passes — both are needed to catch schema regressions:
#
#   1) every playbook against one canonical inventory
#      Catches Jinja typos / undefined refs / role-not-found in any playbook.
#
#   2) playbooks/deploy.yml against every inventory that declares `instances:`
#      Catches inventories whose schema has drifted (e.g. still using the old
#      flat `ipv4:` per-VM after a sweep migrated everything to `networks:`).
#      The structural marker is `^instances:` at any top-level position
#      under a `vars:` block — we approximate with a recursive grep.
#
# Run from the collection root (the dir containing `playbooks/`,
# `inventories/`, `roles/`).
#
# Exit codes:
#   0  all checks passed
#   1  at least one check failed (full list reported at end)

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

CANONICAL_INV="inventories/example-cloud-init.yml"
DEPLOY_PB="playbooks/deploy.yml"

failures=()

# Dummy extras let operational playbooks (add-node, remove-node, pitr, …)
# parse-check without a real -e <var>= at invocation time. These values are
# never actually used by syntax-check — they only satisfy the parser's
# `hosts: "{{ var }}"` strict-undefined check (Ansible 2.19+).
SYNTAX_CHECK_EXTRAS=(
    -e "target_node=__syntax_check__"
    -e "backup_path=/tmp/__syntax_check__"
    -e "target_time=__syntax_check__"
)

run_check() {
    local pb="$1" inv="$2"
    if ! ansible-playbook --syntax-check "${SYNTAX_CHECK_EXTRAS[@]}" -i "$inv" "$pb" >/tmp/sc.out 2>&1; then
        echo "  FAIL: $pb against $inv"
        sed 's/^/    /' /tmp/sc.out
        failures+=("$pb @ $inv")
    fi
}

# Files whose basename starts with `_` are by convention internal partials
# (`import_playbook` targets, not standalone). They have no play header and
# can't be syntax-checked on their own — skip them.
is_partial() {
    [[ "$(basename "$1")" == _* ]]
}

# Operational playbooks whose `hosts:` resolves a dynamically-built group
# (e.g. `groups['mongodb_nodes'][0]`) that's only populated at runtime by
# the corresponding `_setup.yml` partial. They're correct at invocation
# but un-syntax-checkable in isolation under Ansible 2.19+'s strict check.
SKIP_PLAYBOOKS=(
    "playbooks/mongodb/add-node.yml"
    "playbooks/mongodb/remove-node.yml"
    "playbooks/patroni/add-node.yml"
    "playbooks/patroni/remove-node.yml"
)

is_skipped() {
    local pb="$1"
    for s in "${SKIP_PLAYBOOKS[@]}"; do
        [ "$pb" = "$s" ] && return 0
    done
    return 1
}

# ── Pass 1: every playbook against the canonical inventory ─────────────────
echo "=== Pass 1: every playbook × canonical inventory ($CANONICAL_INV) ==="
for pb in playbooks/*.yml playbooks/*/*.yml; do
    [ -e "$pb" ] || continue
    if is_partial "$pb"; then
        echo "  $pb (skip — internal partial)"
        continue
    fi
    if is_skipped "$pb"; then
        echo "  $pb (skip — needs runtime dynamic group)"
        continue
    fi
    echo "  $pb"
    run_check "$pb" "$CANONICAL_INV"
done

# ── Pass 2: deploy.yml against every inventory with `instances:` ───────────
echo
echo "=== Pass 2: $DEPLOY_PB × every inventory declaring 'instances:' ==="
# Find inventories at top level of inventories/ and examples/. Skip
# `inventories/group_vars/` — those are merged into other inventories, not
# usable on their own with `-i`.
mapfile -t inventories < <(
    find inventories examples -maxdepth 1 -type f -name '*.yml' -print0 \
        | xargs -0 grep -lE '^[[:space:]]*instances:[[:space:]]*$' 2>/dev/null \
        | sort
)

if [ ${#inventories[@]} -eq 0 ]; then
    echo "  (none found — is the structural marker still 'instances:'?)"
    exit 1
fi

for inv in "${inventories[@]}"; do
    echo "  $inv"
    run_check "$DEPLOY_PB" "$inv"
done

# ── Pass 3: render tests ───────────────────────────────────────────────────
# Renders every Butane + cloud-init template against representative fixtures
# and asserts the output is parseable YAML. Catches Jinja errors, wrong loop
# var names, and structural bugs that wouldn't surface until a real deploy.
echo
echo "=== Pass 3: render tests (cloud-init + Butane templates) ==="
if ansible-playbook tests/render-templates.yml >/tmp/sc-render.out 2>&1; then
    echo "  render-templates: OK"
else
    echo "  render-templates: FAIL"
    sed 's/^/    /' /tmp/sc-render.out
    failures+=("tests/render-templates.yml")
fi

# ── Report ─────────────────────────────────────────────────────────────────
echo
if [ ${#failures[@]} -eq 0 ]; then
    echo "All checks passed (${#inventories[@]} inventories × deploy.yml + every playbook × $CANONICAL_INV + render tests)."
    exit 0
fi

echo "FAILED: ${#failures[@]} check(s):"
for f in "${failures[@]}"; do
    echo "  - $f"
done
exit 1
