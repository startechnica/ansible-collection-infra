# Smoke tests

Post-deploy verification, structured as **roles** (`startechnica.infra.patroni_smoke`
and `startechnica.infra.mongodb_smoke`) with thin playbook wrappers. Run them
**after `deploy.yml` succeeds** to confirm a freshly-deployed cluster is
actually healthy — not just that the provisioning completed.

## Roles

| Role | Entry-point playbook | What it checks |
|---|---|---|
| `startechnica.infra.patroni_smoke` | `playbooks/test/patroni-smoke.yml` | etcd, patroni, postgres, haproxy, pgbouncer, vip-manager, keepalived, WAL-G, postgres_exporter, TLS |
| `startechnica.infra.mongodb_smoke` | `playbooks/test/mongodb-smoke.yml` | mongod, configsvr, mongos, exporter, PBM agents, scheduled mongodump, TLS |

Each role's `tasks/` directory is split **per component** (one file per service)
plus a `summary.yml` aggregator. Adding a new component (e.g. a future
`chaos.yml` for chaos-day) is a 4-step pattern documented in
`roles/patroni_smoke/tasks/main.yml`.

## When to run

- After the first `deploy.yml` on a new inventory
- After any day-2 operation that could perturb the cluster
  (rolling restart, switchover, cert renewal, mongodb_pbm_enabled flip)
- Before declaring a maintenance window closed
- As a sanity check after fixing anything that touched the deploy

## When NOT to run

- **During** an in-progress deploy
- **During** PITR restore, failover testing, or any other deliberate
  failover — the smoke test asserts *normal* health; it'll flag the failover
- As a CI step on every push — these hit the live cluster, not a sandbox

## Usage

```bash
# Auto-pick the right smoke test from inventory instance_tags:
./scripts/smoke.sh inventories/artaku-db-idc3d.yml --ask-vault-pass

# Force a specific test (ignores tags):
./scripts/smoke.sh inventories/artaku-db-idc3d.yml --patroni-only --ask-vault-pass
./scripts/smoke.sh inventories/artaku-db-idc3d.yml --mongodb-only --ask-vault-pass

# Or run the playbook directly:
ansible-playbook playbooks/test/patroni-smoke.yml -i inventories/<inv>.yml --ask-vault-pass
ansible-playbook playbooks/test/mongodb-smoke.yml -i inventories/<inv>.yml --ask-vault-pass
```

## What each test asserts

### `patroni-smoke.yml` — ~25 positive + negative-as-audit checks

Per-node health:

1. **Containers up** (etcd, patroni, haproxy, vip-manager, keepalived required;
   pgbouncer / postgres-exporter / walg-init gated on their flags)

**etcd** (cluster-wide, run-once):

2. **Endpoint health** per member (`etcdctl endpoint health`)
3. **Member list** via `endpoint status --write-out=json`
4. **Exactly 1 etcd leader** (would catch split-brain — negative-as-audit)

**patroni** (run-once on the leader):

5. **`patronictl list --format json`** parses cleanly
6. **Member count == `groups['patroni_nodes'] | length`** (no missing nodes)
7. **Exactly 1 Leader** (or 1 Standby Leader on standby clusters)
8. **patroni REST `/health` returns 200**
9. **No replica lag > 5s**

**postgresql** (via VIP):

10. **VIP reachable** on `postgresql_port` (TCP-level)
11. **SELECT 1** through the VIP
12. **`pg_is_in_recovery() = false` on the leader**
13. **`pg_hba_file_rules` has replication entries** (replication CIDR allowed)
14. **Write+replicate**: insert on the leader, read on each replica (with cleanup)
15. **Smoke table cleanup** (always — prevents test-data leakage)

**Users + DBs + Owner** (gated on `patroni_smoke_check_users_and_dbs`):

When `patroni_databases` is populated (positive path):

16. **(A) Each DB in `patroni_databases[].name` exists in `pg_database`** —
    one query per run with `WHERE datname = ANY(ARRAY[...])`. Catches: deploy
    didn't run, DB was dropped post-deploy, inventory typo'd a name.
17. **(B) Each user in `patroni_databases[].users[].name` exists in
    `pg_roles` with `rolcanlogin=true`** — catches: user-provisioning
    silently failed (clients would get "role does not exist" on connect).
18. **(C) Each DB's owner matches `patroni_databases[].owner`** — catches:
    silent owner reassignment (someone `GRANT`'d the DB to a different
    role, so app-level "owner" privileges silently degraded).

When `patroni_databases` is empty (negative-as-audit path — verifies the
cluster matches the inventory's empty claim, NOT a mock):

19. **No managed DBs on the cluster** — `SELECT datname FROM pg_database
    WHERE datname NOT IN ('postgres','template0','template1')` returns 0
    rows. Catches: previous inventory had DBs, was reduced to `[]`, but
    the cleanup step never ran or failed silently — the cluster's actual
    state no longer matches the inventory's "no managed DBs" claim.
20. **No managed LOGIN users on the cluster** — `SELECT rolname FROM
    pg_roles WHERE rolcanlogin=true AND rolname NOT IN ('postgres')`
    returns 0 rows. Same catch as above for the user dimension.
    (NOLOGIN-only roles are excluded — they're out-of-band service
    accounts that aren't part of the inventory model.)

The two paths are mutually exclusive (`patroni_databases | length > 0`
vs `== 0`); the empty path runs only when the inventory declares empty.

**haproxy**:

16. **`:5000/stats` reachable**
17. **`postgres-primary` backend UP**
18. **`postgres-replica` backend UP**

**pgbouncer** (when `pgbouncer_enabled`):

19. **`:6432` reachable**

**vip-manager + keepalived**:

20. **Exactly 1 node holds the VIP** (split-brain guard — would catch two nodes
    claiming the same VIP, a classic misconfig)

**WAL-G** (when `walg_enabled`):

21. **`wal-g backup-list` returns at least one entry** (proves backups are
    happening — wouldn't fail on empty if init_backup hasn't run yet)
22. **`walg-cron.timer` is `active`** (scheduled backups running)

**postgres_exporter** (when enabled):

23. **`:9187/metrics` returns 200**

**TLS**:

24. **Patroni server cert** exists and is readable
25. **Patroni CA cert** exists and is readable (certs-within-N-days check is
    done via the mongodb_status module on the mongodb side; for patroni the
    file readability is the gate)

**"Create new cert" — sign+verify flow** (delegated to localhost, runs on
each node using its own SAN, never touches the deployed certs):

26. **Generate leaf key + CSR + sign with the controller-side CA** — exercises
    the same `community.crypto` modules as the deploy role, on a fresh
    subdir under `patroni_local_certs_dir/_smoke_new_cert/<hostname>/`. A
    failure here means the local CA, its key, or the cert generation
    pipeline is broken — the next deploy that needs a new leaf cert would
    fail too. Catches: CA key corruption, expired CA, broken OpenSSL,
    `community.crypto` collection missing/broken on the controller.
27. **`openssl verify` against the deployed CA** — proves the freshly signed
    cert chains back to the CA that patroni is actually using. A failure
    here means there's a CA mismatch (e.g. someone regenerated the CA but
    not the leaves, or there's an old CA file lingering in the artifacts
    dir).
28. **SAN includes this node's hostname + cluster VIP** — proves the SAN
    generator in the role produces the right names for the current
    inventory. Catches: inventory added a new node but the cert template
    wasn't updated; VIP hostname/IP mismatch (would cause clients to fail
    `verify-full`).
29. **Cleanup**: scratch subdir removed at the end (`run_once: true` on
    localhost).

### `mongodb-smoke.yml` — ~20 positive + negative-as-audit checks

**Containers**:

1. **mongod + mongos** up on every node
2. **configsvr** up on every node (sharded only)
3. **exporter** up (when enabled)
4. **PBM agents** up (when enabled; sharded expects 2, replicaset expects 1)

**Cluster state** (via `mongodb_status` module):

5. **mongodb_status module succeeds**
6. **Each RS has exactly 1 primary**
7. **No RS has 0 members**
8. **RS names match expected** (`mongodb_config_replset` + `mongodb_shard_replset`
   on sharded; just the shard RS on replicaset)

**mongos**:

9. **`:27017` reachable** on TCP
10. **`db.runCommand({ping:1}).ok` = 1**

**End-to-end**:

11. **Write through mongos** (insert into unique collection in `startechnica_smoke` DB)
12. **Read from replica** (doc found — replication worked end-to-end)
13. **Smoke DB cleanup** (always — prevents test-data leakage)

**PBM** (when `mongodb_backup_pbm_enabled`):

14. **`pbm status` healthy** (no agents stuck or in error state)
15. **At least one base backup exists** (init_backup worked) when
    `mongodb_backup_init` is true (default)

**Scheduled mongodump**:

16. **`mongodb-backup.timer` is `active`** (when `mongodb_backup_enabled`)

**Certs**:

17. **CA cert > 30 days remaining**
18. **healthcheck client cert > 30 days remaining**

> **What "negative-as-audit" means here:** every check is a read against live
> state (a query, a TCP probe, a `systemd show`, an `openssl x509`). None of
> them intentionally break the cluster. They detect *misconfiguration that
> would only matter under failure* (e.g. exactly-1 VIP holder, exactly-1 RS
> primary, lag within budget) — not chaos.

## Design choices

- **All asserts, then report.** A single transient blip does not mask other
  failures — the operator sees the full picture in one run.
- **Read-mostly.** The write+read probes use a uniquely-named
  collection / table with the smoke timestamp, dropped in an `always:`
  block. On a partial failure the cleanup still runs.
- **Idempotent on second run.** Re-running the smoke test does not break
  anything (the unique collection name avoids collisions).
- **No `mongodb_backup_pbm_enabled`-only run separately.** The MongoDB smoke test
  handles PBM-disabled clusters cleanly (PBM assertion becomes trivially
  PASS — "disabled" is healthy too).

## Limitations

- These tests do **not** exercise HA failover — they assert normal
  cluster health, not the failover path. Use `playbooks/patroni/switchover.yml`
  + a second smoke run for that.
- They do **not** catch every deploy bug. They're an additional layer on top
  of the role's own preflight, template-render tests in `tests/render-templates.yml`,
  and the live deploy flow.
- They depend on credentials being present in the inventory (vault password,
  `postgresql_admin_password`, etc.). No credentials ⇒ skipped checks, not
  a hard fail.

## Adding new checks (or a new component)

Each smoke role is a **dispatcher** (`tasks/main.yml`) that includes
**per-component task files** (`tasks/<component>.yml`). To add a new component
(e.g. a `chaos.yml` for chaos-day) or a single new assert:

1. **Write the collector task**. Use `register: _smoke_<component>` naming so
   the summary can read it. Place it in `roles/<role>/tasks/<component>.yml`.
2. **Wire it in the dispatcher** (`roles/<role>/tasks/main.yml`) with the
   `<role_smoke>_check_<component>` flag (defaulted in `defaults/main.yml`,
   documented in `meta/argument_specs.yml`).
3. **Add the failure clause** to the `_failures` accumulator in
   `tasks/summary.yml` so the aggregator picks it up.
4. **Update this README** with the new component's checks.

For a single new assert within an existing component (e.g. an extra postgres
check), add the collector task to the existing tasks file and the matching
clause to the summary.

## Future: chaos tests

The current roles are **read-only**. Chaos tests (kill etcd leader, drop
network, corrupt cert) need explicit opt-in via a separate flag (e.g.
`smoke_chaos: true`) and a separate set of tasks in `tasks/chaos.yml`. The
role structure lays the ground for this — the dispatcher is single-file and
gated-flag-driven, so adding a chaos block follows the same 4-step pattern.