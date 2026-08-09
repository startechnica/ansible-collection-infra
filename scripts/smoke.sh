#!/usr/bin/env bash
# Run the post-deploy smoke test for an inventory. Picks the right playbook
# based on what the inventory actually deploys (instance_tags).
#
# Usage:
#   scripts/smoke.sh <inventory> [--patroni-only] [--mongodb-only] [<extra-ansible-args>...]
#
# Examples:
#   scripts/smoke.sh inventories/artaku-db-idc3d.yml --ask-vault-pass
#   scripts/smoke.sh inventories/manu.yml --ask-vault-pass --check
#   scripts/smoke.sh inventories/artaku-db-idc3d.yml --patroni-only
#   scripts/smoke.sh inventories/artaku-db-idc3d.yml --mongodb-only -v
set -euo pipefail

INV="${1:-}"
shift || true
if [ -z "$INV" ] || [ ! -f "$INV" ]; then
  echo "usage: $0 <inventory> [--patroni-only|--mongodb-only] [<extra-ansible-args>...]" >&2
  exit 2
fi

# Restrict search to first arg so we don't slurp --patroni-only if it was
# passed before the inventory.
INVENTORY_FILE="$INV"
shift || true

PATRONI_ONLY=0
MONGODB_ONLY=0
PATRONI_RUN=0
MONGODB_RUN=0

for arg in "$@"; do
  case "$arg" in
    --patroni-only) PATRONI_ONLY=1 ;;
    --mongodb-only) MONGODB_ONLY=1 ;;
    *)               :            ;;  # forward to ansible-playbook
  esac
done

# Read the inventory's instance_tags (faster than the full host enumeration).
# --limit '<group>:<host>' is not available without a group; use a service
# that's cheap: ansible-inventory --list parses the inventory once.
TAGS=$(ansible-inventory -i "$INVENTORY_FILE" --list 2>/dev/null \
  | python3 -c '
import json, sys
data = json.load(sys.stdin)
tags = set()
for host, attrs in data.items():
    if host == "_meta" or not isinstance(attrs, dict):
        continue
    for t in (attrs.get("instance_tags") or []):
        tags.add(t)
print(",".join(sorted(tags)))')

if [ $PATRONI_ONLY -eq 1 ]; then
  PATRONI_RUN=1
fi
if [ $MONGODB_ONLY -eq 1 ]; then
  MONGODB_RUN=1
fi
if [ $PATRONI_RUN -eq 0 ] && [ $MONGODB_RUN -eq 0 ]; then
  case ",${TAGS}," in
    *,patroni,*)  PATRONI_RUN=1 ;;
  esac
  case ",${TAGS}," in
    *,mongodb,*)  MONGODB_RUN=1 ;;
  esac
fi

PLAYBOOK_DIR="$(cd "$(dirname "$0")/../playbooks/test" && pwd)"
EXTRA_ARGS=("$@")

if [ $PATRONI_RUN -eq 1 ]; then
  echo ">>> smoke.sh: running patroni smoke test"
  ansible-playbook "${PLAYBOOK_DIR}/patroni-smoke.yml" \
    -i "$INVENTORY_FILE" "${EXTRA_ARGS[@]}"
fi

if [ $MONGODB_RUN -eq 1 ]; then
  echo ">>> smoke.sh: running mongodb smoke test"
  ansible-playbook "${PLAYBOOK_DIR}/mongodb-smoke.yml" \
    -i "$INVENTORY_FILE" "${EXTRA_ARGS[@]}"
fi

if [ $PATRONI_RUN -eq 0 ] && [ $MONGODB_RUN -eq 0 ]; then
  echo ">>> smoke.sh: no patroni/mongodb tags in instance_tags ($TAGS) — nothing to test" >&2
  exit 1
fi
