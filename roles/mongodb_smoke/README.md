# mongodb_smoke

Read-only, post-deployment health verification for a running MongoDB sharded
cluster or replica set. The role imports the MongoDB role's exported variables,
so it uses the deployed container engine, topology, replica-set names, ports,
feature flags, and TLS paths rather than maintaining a separate copy of that
configuration. It does **not** install, reconfigure, restart, elect a primary,
or restore MongoDB.

Use `startechnica.infra.mongodb_smoke` after deployment and after maintenance to
collect an aggregated PASS/FAIL report for the live cluster.

## What it verifies

| Area | Checks | When it applies |
|---|---|---|
| Containers | `mongodb-mongod` and `mongodb-mongos` on every node; `mongodb-configsvr` for sharded clusters; exporter and PBM agent containers when enabled. | `mongodb_smoke_check_containers` |
| Replica-set state | The `mongodb_status` module reads replica sets, core containers, and certificate lifetime. The summary requires each expected replica set to have members and exactly one primary. | `mongodb_smoke_check_cluster_state` |
| mongos | The configured mongos port accepts TCP connections and a TLS/X.509-authenticated `db.runCommand({ ping: 1 })` succeeds. | `mongodb_smoke_check_mongos` |
| End-to-end data path | Inserts a uniquely named test document through mongos, waits for replication, reads it back, and removes the smoke database in an `always` block. | `mongodb_smoke_check_endtoend` |
| Percona Backup for MongoDB (PBM) | PBM agent status and base-backup list, using the deployment role's PBM control URI. | Only when `mongodb_backup_pbm_enabled` is true. |
| Scheduled backups | `mongodb-backup.timer` is active and `/var/log/mongodb-backup.log` exists. | Only when `mongodb_backup_enabled` is true. |
| TLS | The CA and health-check client certificate are readable. Certificate expiry is included in the `mongodb_status` data. | `mongodb_smoke_check_tls` |

All collectors use `failed_when: false` where possible. This lets the role
report the complete failure set in `summary.yml` and fail only at the final
PASS/FAIL aggregation step.

## Requirements

- A previously deployed MongoDB cluster with the `mongodb_nodes` group built
  from inventory `instances`.
- The collection dependencies used by the checks, including `containers.podman`
  and the collection's `mongodb_status` module.
- Podman and `mongosh` in the deployed MongoDB containers; the role uses the
  X.509 health-check credentials created by the deployment role.
- For the end-to-end and mongos checks, a reachable configured mongos listener.

## Usage

Run the collection playbook directly:

```bash
ansible-playbook startechnica.infra.mongodb_smoke \
  -i inventories/<inventory>.yml --ask-vault-pass
```

Or use the repository helper:

```bash
./scripts/smoke.sh inventories/<inventory>.yml --mongodb-only --ask-vault-pass
```

The equivalent source-tree wrapper is `playbooks/mongodb_smoke.yml`. It first
runs `mongodb/_setup.yml` on the controller to build `mongodb_nodes`, then runs
this role on those hosts with privilege escalation.

## Configuration

Override role settings in inventory, group variables, or play variables.

| Variable | Default | Purpose |
|---|---:|---|
| `mongodb_smoke_enabled` | `true` | Master switch. `false` makes the role a no-op. |
| `mongodb_smoke_replication_wait_seconds` | `5` | Delay between the smoke write and the matching read. |
| `mongodb_smoke_db` | `startechnica_smoke` | Database created for the end-to-end probe, then removed. |
| `mongodb_smoke_collection` | timestamped name | Unique collection for the end-to-end document. |
| `mongodb_smoke_cert_min_days` | `30` | Minimum remaining certificate lifetime accepted by the summary. |

Each check family can be disabled independently:

```yaml
mongodb_smoke_check_containers: true
mongodb_smoke_check_cluster_state: true
mongodb_smoke_check_mongos: true
mongodb_smoke_check_endtoend: true
mongodb_smoke_check_pbm: true
mongodb_smoke_check_mongodump_timer: true
mongodb_smoke_check_tls: true
```

For example, to verify basic cluster health while skipping the deliberate test
write:

```yaml
mongodb_smoke_check_endtoend: false
```

The role deliberately does not duplicate MongoDB deployment settings such as
`mongodb_cluster_type`, replica-set names, port mappings, TLS location, or PBM
configuration. They are imported from the `mongodb` role through its
`export_vars` task, so inventory overrides remain the single source of truth.

## Operational notes

- Run after a successful deployment, rolling restart, backup/PBM change, TLS
  renewal, node-topology change, or before closing a maintenance window.
- Do not run during a restore, resharding, planned primary-election test, or
  active deployment; the role asserts normal steady-state health.
- The end-to-end probe writes a single document into `mongodb_smoke_db` and
  always drops that database afterward. It is not a substitute for application
  functional testing.
- PBM checks are skipped unless PBM is enabled. The backup-list condition is
  meaningful only after an initial base backup has been created.
- The scheduled mongodump checks are skipped unless `mongodb_backup_enabled` is
  enabled.
- The role supports both `sharded` and `replicaset` topologies; config-server
  checks are automatically omitted for replica-set deployments.

See [the smoke-test guide](../../playbooks/test/README.md) for shared runbook
advice and the full check inventory.
