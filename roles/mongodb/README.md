# mongodb

Deploy and operate a Docker-based MongoDB cluster — sharded or replicaset —
with TLS (X.509 auth), Prometheus exporter, local + S3 backups, and a
comprehensive day-2 surface (rolling restart, upgrade, add/remove node, PITR).

## Features

- **Two topologies** — `sharded` (configsvr + mongos + shard) or `replicaset` (single RS).
- **TLS everywhere** — ECDSA certs by default (P-256), with RSA fallback. CA + per-host member certs + client certs for healthcheck and exporter.
- **X.509 auth** for cluster members, healthchecks, and the Prometheus exporter — no passwords in memory for system roles.
- **Auto-generated admin password** when `mongodb_admin_password` is empty, persisted to the controller's artifacts dir.
- **Applications DB/user provisioning** via the `mongodb_databases` list.
- **Backups** — local mongodump + optional upload to S3/MinIO via a throwaway `mc` container (no host-side dependency).
- **Day-2 playbooks** — backup, verify-backup, restore, PITR, rolling restart, add-node, remove-node, rolling upgrade, cert renewal.

## Requirements

- Ansible 2.16+ (tested on 2.19)
- Docker 24+ on each target node (install via the bundled `geerlingguy.docker` role or separately)
- Python on controller: `community.crypto` (TLS cert generation) + `cryptography`.
  See [requirements.txt](../../requirements.txt).
- Python on targets: `pymongo >= 4.6` + `docker` SDK — installed automatically
  by the `preflight` dependency into `/opt/ansible-venv` (no system
  Python pollution).
- ≥ 3 nodes for a production replica set (quorum)
- Time synced across nodes (chrony/NTP) — replica sets are sensitive to clock drift

## Role dependencies

Declared in [meta/main.yml](meta/main.yml):

- **`preflight`** — Docker reachability, cryptography lib, RAM sanity.

## Usage

### Via the single-service playbook

```bash
ansible-playbook playbooks/mongodb/install.yml \
  -i inventories/<inv>.yml --diff
```

That playbook bundles: `_setup.yml` (NetBox enrichment + `mongodb_nodes` group + `artifacts_dir`) → role execution.

### Via the full-stack playbook

```bash
ansible-playbook playbooks/deploy.yml -i inventories/<inv>.yml --diff
# or: ansible-playbook startechnica.infra.deploy -i inventories/<inv>.yml --diff
```

Routes VMs tagged `mongodb` (in NetBox or via inventory `instance_tags`) to this role.

### Direct role include

```yaml
- hosts: mongodb_nodes
  become: true
  roles:
    - role: startechnica.infra.mongodb
      vars:
        mongodb_cluster_type: replicaset
        mongodb_version: "8.2.6"
        mongodb_admin_password: "{{ vault_mongodb_admin_password }}"
```

## Inventory shape

Minimum config under the host group or `cloud_init.vars`:

```yaml
mongodb_cluster_type: sharded        # or replicaset
mongodb_version: "8.2.6"
mongodb_admin_password: ""           # auto-generated if empty

# (optional) application DBs
mongodb_databases:
  - name: app_main
    users:
      - name: app_main
        password: "{{ vault_app_main_password }}"
        roles:
          - { role: readWrite, db: app_main }
      - name: app_main_ro
        password: "{{ vault_app_main_ro_password }}"
        roles:
          - { role: read, db: app_main }

# (optional) S3 backup target — shared with patroni
s3_endpoint: "https://s3.example.com"
s3_bucket: "backups"
s3_access_key: "{{ vault_s3_access_key }}"
s3_secret_key: "{{ vault_s3_secret_key }}"
```

Full variable reference: [meta/argument_specs.yml](meta/argument_specs.yml).

## Architecture

### Sharded topology (default)

```
             ┌─── mongos (client entry) :27017
             │
             ├─── configsvr RS :27018 (config metadata)
             │
             └─── shard RS (mongod) :27019 (data)
```

- Each node runs three containers: `mongodb-mongod`, `mongodb-configsvr`, `mongodb-mongos`.
- Clients connect to `mongos` on 27017, which transparently routes to the correct shard.
- configsvr holds cluster metadata; a single shard is created for simple deployments.

### Replicaset topology

```
             ┌─── mongod RS :27017 (data)
```

Single `mongodb-mongod` container per node; no mongos/configsvr. Simpler for smaller deployments that don't need sharding.

### TLS / X.509 auth

Certs generated on the controller, distributed to each node at `{{ mongodb_root_dir }}/ssl/`:

| File | Purpose |
|---|---|
| `ca.pem` + `ca.key` | Self-signed CA |
| `mongodb-mongod.pem` | mongod server cert (per host) |
| `mongodb-configsvr.pem` | configsvr server cert (per host, sharded only) |
| `mongodb-mongos.pem` | mongos server cert (per host, sharded only) |
| `healthcheck.pem` | Client cert for healthchecks (X.509 auth) |
| `exporter.pem` | Client cert for mongodb_exporter (X.509 auth) |

X.509 client auth users (auto-created):
- `{{ mongodb_healthcheck_x509_subject }}` → `clusterMonitor`
- `{{ mongodb_exporter_x509_subject }}` → `clusterMonitor` + `read` on `local`

### Backup flow

1. `mongodump` runs via the custom `mongodb_backup` module (writes to `{{ mongodb_backup_dir }}`).
2. Local retention: files older than `{{ mongodb_backup_retain_days }}` pruned.
3. If `mongodb_backup_mode: s3`: tar-gzip + upload via containerized `minio/mc`.
   The object is then re-read and **SHA-256 compared end-to-end** against the
   local archive; only on a match is the local dump removed (pure-S3 — the
   bucket is the only copy). A mismatch fails the run and keeps the local copy.
4. Remote retention: S3 objects older than `{{ mongodb_backup_retain_days }}` pruned.

`mongodb_backup_mode` selects the destination — `local` (node only, kept under
`mongodb_backup_retain_days`) or `s3` (dump locally, upload, then delete the
local copy so the bucket is the system of record). It defaults to `s3` when
`s3_bucket` is set, else `local`.

**Scheduled backups** — provisioning installs a host-level systemd timer
(`mongodb-backup.timer` → `mongodb-backup.service`) on one node that runs the
same dump → prune → S3-upload flow on `mongodb_backup_schedule` (default
`*-*-* 02:00:00`, i.e. daily at 02:00). This mirrors patroni's
`walg-cron.timer`. `Persistent=true` catches up a missed run after downtime;
output is appended to `/var/log/mongodb-backup.log`. Set
`mongodb_backup_enabled: false` (or `mongodb_backup_schedule: ""`) to disable
the timer — it's torn down on the next provision run; on-demand
`playbooks/mongodb/backup.yml` still works. Inspect with
`systemctl list-timers mongodb-backup.timer` and
`journalctl -u mongodb-backup.service`.

Verify a backup is restorable:
```bash
ansible-playbook playbooks/mongodb/verify-backup.yml -i inventories/<inv>.yml
# Or pull from S3:
ansible-playbook playbooks/mongodb/verify-backup.yml -i inventories/<inv>.yml -e backup_source=s3
```

**Schedule verification** — catching silent corruption early matters more than
taking the backup in the first place. Schedule from the Ansible controller via
cron, GitLab CI scheduled pipeline, Ansible Tower/AWX template, or any other
scheduler you already use. A reference crontab is at
[examples/crontab.example](../../examples/crontab.example).

## Day-2 operations

| Operation | Command |
|---|---|
| Cluster status | `playbooks/mongodb/status.yml` |
| On-demand backup (local + S3 if configured) | `playbooks/mongodb/backup.yml` |
| Verify the latest backup restores cleanly | `playbooks/mongodb/verify-backup.yml` |
| Restore from a specific mongodump | `playbooks/mongodb/restore.yml -e restore_path=...` |
| Point-in-time recovery (oplog replay) | `playbooks/mongodb/pitr.yml -e backup_path=... -e target_time=...` (or `-e backup_source=s3`) |
| Rolling restart | `playbooks/mongodb/restart.yml` |
| Renew leaf certificates | `playbooks/mongodb/renew-certs.yml` |
| Rolling version upgrade | `playbooks/mongodb/upgrade.yml -e mongodb_image_tag_new=8.2.7` |
| Scale up (add replica) | `playbooks/mongodb/add-node.yml -e target_node=<host>` |
| Scale down (remove replica) | `playbooks/mongodb/remove-node.yml -e target_node=<host>` |
| Uninstall | `playbooks/mongodb/uninstall.yml [-e mongodb_destroy_prune=true]` |

## Variables reference

Inputs are validated by [meta/argument_specs.yml](meta/argument_specs.yml). Highlights:

| Category | Key variables |
|---|---|
| Topology | `mongodb_cluster_type`, `mongodb_version` |
| Identity | `mongodb_uid`/`_gid`, `mongodb_pki_o`/`_member_ou`/`_client_ou` |
| TLS | `tls_key_type`, `tls_key_curve`, `ssl_days`, `ssl_ca_days` |
| Admin | `mongodb_admin_user`, `mongodb_admin_password` (auto-gen if empty) |
| App DBs | `mongodb_databases` (list of {name, users[{name, password, roles[]}]}) |
| Backup local | `mongodb_backup_dir`, `mongodb_backup_retain_days`, `mongodb_backup_pitr`, `mongodb_backup_enabled`, `mongodb_backup_schedule`, `mongodb_backup_mode` |
| Backup S3 | `s3_bucket`, `s3_endpoint`, `s3_access_key`, `s3_secret_key`, `s3_prefix` |
| Monitoring | `mongodb_exporter_enabled`, `mongodb_exporter_port` |
| Uninstall | `mongodb_destroy_prune`, `mongodb_skip_confirm` |

## Artifacts (controller-side)

Written to `playbooks/artifacts/<inventory-stem>/mongodb/`:

- `admin.password` — the admin password (0600, regenerated only if file deleted)
- `certs/` — the full PKI (CA, member certs, client certs)
- `certs/<hostname>/<cert>.pem` — per-host member certs
- Per-DB/user credential files (generated when `mongodb_databases` is set)

## Troubleshooting

- **`permission denied` on exporter.pem** — fixed by running the exporter container as `mongodb_uid:mongodb_gid`. Check `docker-compose.yml` for `user: "999:999"`.
- **`atlascli` probe in configsvr logs** — benign. mongosh probes for Atlas-specific features; we disable the noise by passing `--quiet` everywhere.
- **`cloud-config failed schema validation (users_groups)`** — cloud-init user-data bug; ensure `instance_user_ssh_authorized_keys` is not an empty list in the VM template.
- **Replica set unhealthy after node add** — oplog window may be too narrow. Check `rs.printSecondaryReplicationInfo()` on a healthy member.

## Gotchas

- **MongoDB 8 breaks on Linux kernel ≥ 6.19** — `mongod` refuses to start
  (`MongoDB cannot start: Linux kernel versions 6.19 and newer has a known
  incompatibility...`). The `preflight` task
  ([tasks/preflight.yml](tasks/preflight.yml)) asserts the kernel is `< 6.19`
  before install. **The kernel bump lands WITHIN a single FCOS major release**,
  so pinning FCOS 43 alone is not enough:

  | FCOS build | Kernel | MongoDB 8 |
  |---|---|---|
  | `43.20260217.3.1` | 6.18 | ✅ works |
  | `43.20260413.3.2` | 6.19 | ❌ broken |

  Pin the host to an FCOS build with kernel `< 6.19`, or bypass with
  `mongodb_skip_kernel_check: true` **only** after verifying mongo actually runs
  on your kernel — running with the check disabled on an incompatible kernel
  crashes containers in a tight loop.
- **FCV (featureCompatibilityVersion)** is NOT auto-bumped on version upgrade. After a major upgrade (6→7, 7→8), run `db.adminCommand({setFeatureCompatibilityVersion: "7.0"})` manually after a soak period.
- **Auto-generated admin password persists** — once `admin.password` exists in the artifacts dir, it's reused on every run. Delete the file if you want a fresh password.
- **Cert rotation is zero-downtime** — renew-certs.yml uses a rolling restart, one node at a time. The CA is NOT rotated unless you explicitly do so (breaking change).
- **Uninstall is dual-gated** — there are TWO interactive prompts, both asking you to type `prune`:
  1. *First prompt* (always): confirms removal of containers, networks, and config files.
  2. *Second prompt* (only when `mongodb_destroy_prune: true`): confirms deletion of the data directory `mongodb_root_dir`. Typing anything other than `prune` here keeps the data on disk even though the flag was set — last-chance escape hatch before an irreversible wipe.

  Use `mongodb_skip_confirm: true` to bypass BOTH prompts in CI/automation. Without `mongodb_destroy_prune: true`, only the first prompt fires and data is preserved.

## See also

- [roles/common/tasks/validate_inventory.yml](../common/tasks/validate_inventory.yml) — asserts inputs before install (run via `deploy.yml --tags validate`).
- [roles/patroni](../patroni/) — sibling HA-postgres role.
- [roles/netbox_lookup](../netbox_lookup/) / [roles/netbox_register](../netbox_register/) — NetBox I/O.
