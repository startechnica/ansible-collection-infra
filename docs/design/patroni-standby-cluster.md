# Design: Patroni standby-cluster support (DR / off-site replica)

Status: **proposed** — not yet implemented.
Target: 1.0.4 (or a later 1.0.3 slice)
Author: scoped during `fix/walg`

Two design decisions are locked (per review):

- **Replication = streaming with WAL-G fallback.** The standby's `standby_cluster`
  block streams from the primary's IP/port and falls back to `restore_command`
  (WAL-G `wal-fetch` from the shared S3 archive) when the stream breaks. This is
  Patroni's recommended production setup and reuses the WAL-G infra already present.
- **Promotion = manual playbook.** A dedicated `playbooks/patroni/standby-promote.yml`
  removes the `standby_cluster` config (via `patronictl edit-config`) and reloads,
  converting the standby into an independent primary. DR activation must be deliberate.
- **TLS trust = support both paths** (shared CA and scram+`sslmode`), documented with
  the trade-off.
- **Scope = full feature** — all sections below land together.

## Why

The Patroni role deploys a **single** HA PostgreSQL cluster: one `patroni_scope`,
one etcd DCS, one member set from `groups['patroni_nodes']`. There is no way to
stand up a **second** cluster that continuously replicates from the first for DR
or off-site read replicas.

A **standby cluster** is a full Patroni cluster (its own etcd, its own
`patroni_scope`, its own nodes) whose leader is a **"Standby Leader"** that replays
the primary's data instead of accepting writes. On a DR event it is **promoted**
to an independent primary.

Exploration confirmed the building blocks exist (etcd identity is scope-isolated,
WAL-G plumbing is reusable, cert-gen machinery is present) but there are real
**cross-cluster gaps** this design must close — see "Gotchas".

## Key facts from exploration (file:line)

- `roles/patroni/templates/patroni.yml.j2` — no `standby_cluster` block today.
  `bootstrap.dcs` is lines 21-79; a `standby_cluster:` must be a sibling of
  `postgresql:` under `bootstrap.dcs` (~line 27). `restore_command` wal-fetch
  wiring at :46-52. Replication auth block at :122-137, replication pg_hba loop at
  :86-92 (node_ips only). `post_bootstrap: CREATE ROLE replicator` at :80.
- `roles/patroni/tasks/shared/reconcile_archiving.yml` — **the model to copy.**
  Post-bootstrap DCS-reconcile pattern: detect leader → `patronictl show-config` →
  diff → `patronictl edit-config --force --set` only when different. `standby_cluster`
  is DCS-only (seeds at bootstrap), so it needs the same treatment.
- `roles/patroni/tasks/shared/_computed_vars.yml:7-40` — derives `node_ips`,
  `walg_enabled`, etc. from `groups['patroni_nodes']`. Natural home for a resolved
  `_standby_*` fact + early asserts.
- `roles/patroni/tasks/{podman,docker}/_detect_leader.yml` — **hard-asserts exactly
  one `Role == 'Leader'`** (podman :23, :54). A standby cluster's top node is
  `Standby Leader`, so these tasks fail on a standby unless made standby-aware.
- `roles/patroni/tasks/shared/tls_certificates.yml:83` — CA CN = `{{ patroni_scope }}-ca`,
  self-signed **per cluster**, generated on localhost (`patroni_local_certs_dir`) then
  pushed to nodes. Cross-cluster streaming TLS (`verify-ca`) fails because the standby
  trusts a different CA than the primary's server cert.
- `roles/patroni/templates/walg.env.j2:9` — `WALG_S3_PREFIX=s3://{{ s3_bucket }}`
  (bucket root, **not** keyed by scope). A standby can wal-fetch the primary's archive
  only because there's no scope isolation — but the standby must point WAL-G at the
  **primary's** prefix, and two independent clusters sharing a bucket would collide.
- `roles/firewall/defaults/main.yml:91-93` — `postgresql-direct` (port 55432)
  restricted to `groups['patroni_nodes']`. A remote standby's IP is excluded on the
  primary's firewall.
- `roles/patroni/tasks/main.yml` — role entry; provision path runs `_computed_vars` →
  preflight → directories/tls/configs → `{{ engine }}/deploy.yml` →
  walg/reconcile/dbs. `patroni_action` drives day-2 dispatch (:194-230).

## Approach

Add standby support as an **opt-in mode** toggled by a single flag, reusing the
existing provision flow. A standby cluster is deployed exactly like a normal cluster
(own inventory, own `patroni_scope`, own nodes) plus a `standby_cluster` config block
and the cross-cluster wiring. No new engine paths; all changes ride the existing
`shared/` + template structure.

### 1. New variables — `roles/patroni/defaults/main.yml` + `meta/argument_specs.yml`

Add a "Standby cluster" section (follow the `# ──` header + folded-description style):

```yaml
patroni_standby_enabled: false            # master switch: this cluster is a standby
patroni_standby_primary_host: ""          # remote primary IP/host to stream from
patroni_standby_primary_port: 55432       # remote primary postgresql_port
patroni_standby_primary_slot: ""          # optional physical slot name on the primary
patroni_standby_primary_sslmode: verify-ca  # verify-ca | require (see §6b)
patroni_standby_create_replica_methods:   # order: try basebackup, fall back to WAL-G
  - basebackup
patroni_standby_primary_walg_prefix: ""   # "" = same s3_bucket root as primary (§6)
patroni_shared_ca_dir: ""                 # "" = mint per-scope CA; set = reuse shared CA (§6a)
patroni_replication_cidrs: []             # primary-side: extra replication pg_hba entries (§6)
```

Declare each in `argument_specs.yml` with `type`/`default`/`description`, matching the
file's conventions (bool/str/int; `list` + `elements: str` for the methods/CIDR lists;
`choices` for `sslmode`).

### 2. Config template — `roles/patroni/templates/patroni.yml.j2`

Under `bootstrap.dcs` (sibling of `postgresql:`, ~line 27), add a gated block:

```jinja
{% if patroni_standby_enabled | default(false) | bool %}
    standby_cluster:
      host: {{ patroni_standby_primary_host }}
      port: {{ patroni_standby_primary_port }}
{% if patroni_standby_primary_slot | length > 0 %}
      primary_slot_name: {{ patroni_standby_primary_slot }}
{% endif %}
      create_replica_methods:
{% for m in patroni_standby_create_replica_methods %}
        - {{ m }}
{% endfor %}
{% if walg_enabled %}
      restore_command: "/opt/walg/wal-g wal-fetch %f %p"
{% endif %}
{% endif %}
```

Also:

- When `patroni_standby_enabled`, **skip** `post_bootstrap: CREATE ROLE replicator`
  (:80) — a standby clones the primary (which already has the role); recreating it
  errors. Gate line 80 on `not patroni_standby_enabled`.
- The standby's outbound stream honors `patroni_standby_primary_sslmode` (§6b).
- Primary-side: add an optional `patroni_replication_cidrs` loop emitting
  `hostssl replication replicator {{ cidr }} …` (mirrors `postgresql_admin_cidrs`),
  so the primary's pg_hba admits remote standby IPs.

### 3. Post-bootstrap reconcile — new `roles/patroni/tasks/shared/reconcile_standby.yml`

Copy the shape of `reconcile_archiving.yml`. Because `standby_cluster` only seeds at
bootstrap, this makes it idempotent afterward and is also what a **promote** removes:

- Detect leader (standby-aware — §5).
- `patronictl show-config` → read current `standby_cluster`.
- Desired = the block from the vars (or **absent** when `patroni_standby_enabled=false`).
- `patronictl edit-config --force --set standby_cluster.host=…` (or `--set standby_cluster=null`
  to remove) only when different.

Wire into `main.yml` right after `reconcile_archiving.yml` (:172-190 area).

### 4. Promotion playbook — new `playbooks/patroni/standby-promote.yml`

Follow the day-2 wrapper convention (`_setup.yml` → `patroni_nodes` play → role with a
new `patroni_action: standby-promote`). Action path:

- Confirm (reuse `uninstall_confirm.yml` pattern / `patroni_skip_confirm`).
- `patronictl edit-config --force --set standby_cluster=null` on the standby leader →
  Patroni promotes the Standby Leader to a real primary.
- Output guidance to flip `patroni_standby_enabled=false` in inventory so a later
  re-provision doesn't re-attach. Add `standby-promote` to the `patroni_action`
  dispatch in `main.yml:194-230`.

### 5. Standby-aware leader detection — `roles/patroni/tasks/{docker,podman}/_detect_leader.yml`

The `until`/`fail`/`selectattr` require exactly one `Role == 'Leader'`. On a standby
the role is `Standby Leader`. Change the selector to accept either:
`selectattr('Role', 'in', ['Leader', 'Standby Leader'])`, and update the fail-diagnostic
text. Required, or reconcile/backup/status day-2 tasks break on any standby.

### 6. Cross-cluster wiring (the gaps)

- **TLS trust (streaming)** — support **both**, pick per deployment:
  - **(a) Shared CA** — `patroni_shared_ca_dir`. When set, `tls_certificates.yml` skips
    minting `{{ patroni_scope }}-ca` and copies/uses that existing CA (key+cert), so both
    clusters chain to one CA and `verify-ca` succeeds. Cleanest; requires deploying both
    clusters from the same controller (or distributing the CA). Recommended when
    same-controller.
  - **(b) scram + relaxed verify** — `patroni_standby_primary_sslmode` (`verify-ca`|`require`).
    With `require` the stream is encrypted but the primary's server cert is not CA-verified,
    so no shared CA is needed; pair with `postgresql_replication_password` on both. Default
    the standby stream to `verify-ca`; operator relaxes to `require` when clusters can't
    share a CA.
- **Replication credentials** — the standby must be handed the **same**
  `postgresql_replication_password` (or replicator cert) as the primary. Document; ensure
  the var flows into the standby_cluster stream auth.
- **Primary-side pg_hba + firewall** — the **primary** must allow the standby's IP for
  `replication`. `patroni_replication_cidrs` covers pg_hba (§2). Document adding the
  standby IPs to the primary's `firewall_service_source_map` / `firewall_trusted_sources`
  for `postgresql-direct` (port 55432).
- **WAL-G prefix** — if standby `s3_bucket` == primary's, wal-fetch works as-is. If the
  primary used a distinct prefix, wire `patroni_standby_primary_walg_prefix` into a
  standby-specific `WALG_S3_PREFIX` in `walg.env.j2` (fall back to `s3://{{ s3_bucket }}`).

### 7. Guardrails — `roles/patroni/tasks/shared/` (assert early)

Add asserts (in `_computed_vars.yml` or a small `validate_standby.yml`):

- `patroni_standby_enabled` ⇒ `patroni_standby_primary_host` non-empty.
- `patroni_standby_enabled` ⇒ `patroni_scope` **differs** from the primary's scope (a
  standby can't self-reference; document that the operator sets a distinct scope).
- Warn if `patroni_standby_enabled` and neither streaming reachability nor `walg_enabled`
  — the standby would have no data source.

### 8. Docs — `roles/patroni/README.md`, `README.md`, `CHANGELOG.md`

- Patroni README: new "Standby cluster (DR)" section — the streaming+WAL-G model, the two
  TLS-trust options, required primary-side wiring (pg_hba CIDR + firewall), and the promote
  playbook. Gotchas: distinct `patroni_scope`, shared-CA vs `sslmode=require`.
- Root README troubleshooting: standby not catching up (TLS/CA mismatch, firewall,
  replication password).
- CHANGELOG `1.0.3 (unreleased)` (or the target version) → `### Added`: standby-cluster support.

## Critical files

- `roles/patroni/templates/patroni.yml.j2` — `standby_cluster` block, gate `post_bootstrap`, optional replication CIDRs, `sslmode`
- `roles/patroni/defaults/main.yml`, `roles/patroni/meta/argument_specs.yml` — new `patroni_standby_*` / `patroni_shared_ca_dir` / `patroni_replication_cidrs` vars
- `roles/patroni/tasks/shared/reconcile_standby.yml` (new) — post-bootstrap DCS reconcile (model: `reconcile_archiving.yml`)
- `roles/patroni/tasks/{docker,podman}/_detect_leader.yml` — accept `Standby Leader`
- `roles/patroni/tasks/main.yml` — wire `reconcile_standby` + `standby-promote` action
- `playbooks/patroni/standby-promote.yml` (new) — manual promotion
- `roles/patroni/tasks/shared/tls_certificates.yml` — optional shared-CA path
- `roles/patroni/templates/walg.env.j2` — optional standby WAL-G prefix
- Docs: `roles/patroni/README.md`, `README.md`, `CHANGELOG.md`

## Verification

- **Lint:** `ansible-lint` (production profile) on every changed/new file — this repo holds
  all patroni files to production profile.
- **Template render:** render `patroni.yml.j2` for (a) normal cluster — no `standby_cluster`,
  `post_bootstrap` present; (b) `patroni_standby_enabled=true` — `standby_cluster` block with
  host/port/create_replica_methods/restore_command, no `post_bootstrap`. Assert with a
  localhost template play + `from_yaml` structure checks.
- **Reconcile idempotency:** dry check that `reconcile_standby.yml` computes desired==current
  on a second run (no `edit-config`).
- **Leader detection:** unit-test the selector against a mocked `patronictl list` JSON where
  the role is `Standby Leader` — assert it resolves the leader instead of failing.
- **Live (optional, if a two-cluster test bed exists):** deploy a standby inventory pointing
  at an existing primary; confirm `patronictl list` shows `Standby Leader` + replicas,
  replication lag advances, then run `standby-promote.yml` and confirm it becomes a normal
  primary.
- **No regression:** re-run a normal single-cluster provision (or its molecule scenario) to
  confirm the gated changes are inert when `patroni_standby_enabled=false`.

## Notes

- Only edit `ansible-collection-infra`; the vendored skydata-sites copy is synced by the user.
- Full feature lands together (deploy + reconcile + promote + standby-aware leader detection +
  both TLS paths + guardrails + docs); build and lint-verify section by section, with
  template-render and leader-selector unit checks before any live test.
