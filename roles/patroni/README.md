# patroni

Deploy and operate a Patroni HA PostgreSQL cluster with etcd (DCS), HAProxy
(read/write routing), PgBouncer (connection pooling), and a floating VIP
via vip-manager or keepalived. TLS on every connection, WAL-G backups,
point-in-time recovery, extension + DB/user provisioning, and a full day-2
surface.

## Features

- **Patroni-managed PostgreSQL** with automatic failover and leader election via etcd.
- **3-layer client routing** — vip-manager (floating IP) → HAProxy (health-aware primary/replica split) → PgBouncer (per-node connection pool) → PostgreSQL.
- **TLS everywhere** — etcd peers, etcd clients, Patroni REST API, PostgreSQL clients, replication. ECDSA by default (P-384), sha384 digest.
- **Auto-generated postgres password** when empty, persisted to controller's artifacts dir.
- **Extensions** — `pg_stat_statements` loaded by default; add others (pgaudit, postgis, timescaledb, ...) via `patroni_extensions` **only when the `patroni_image` actually bundles the corresponding shared library**.
- **Application DB/user provisioning** via `patroni_databases` (uses `community.postgresql` modules through HAProxy to the current primary).
- **Backups** — pg_basebackup (local) or WAL-G (continuous + archive to S3).
- **Day-2 playbooks** — backup, verify-backup, PITR, rolling restart, switchover, renew-certs, rotate-passwords, add/remove node.

## Requirements

- Ansible 2.16+ (tested on 2.19)
- Docker 24+ on each target
- Python on controller: `cryptography` + `community.postgresql >= 3.4.0` +
  `community.docker >= 3.13.0` collections.
- Python on targets: `psycopg2-binary` + `docker` SDK — installed automatically
  by the `preflight` dependency into `/opt/ansible-venv` (no system
  Python pollution).
- ≥ 3 nodes for production (etcd quorum)
- Time synced across nodes (chrony/NTP)

## Role dependencies

Declared in [meta/main.yml](meta/main.yml):

- **`preflight`** — Docker + compose reachability.

## Usage

### Single-service playbook

```bash
ansible-playbook playbooks/patroni/install.yml \
  -i inventories/<inv>.yml --diff
```

### Full-stack playbook

```bash
ansible-playbook playbooks/deploy.yml -i inventories/<inv>.yml --diff
# or: ansible-playbook startechnica.infra.deploy -i inventories/<inv>.yml --diff
```

VMs tagged `patroni` (NetBox or inventory `instance_tags`) get routed to this role.

### Direct role include

```yaml
- hosts: patroni_nodes
  become: true
  roles:
    - role: startechnica.infra.patroni
      vars:
        patroni_scope: myproject-pg
        patroni_vip_address: "10.0.0.100"
        postgresql_postgres_password: "{{ vault_postgres_password }}"
```

## Inventory shape

Required variables (under the host group or `cloud_init.vars`):

```yaml
# --- Cluster identity ---
patroni_scope: myproject-pg        # unique per Patroni cluster sharing etcd

# --- Floating IP (required when vip_manager is enabled) ---
patroni_vip_address: "10.0.0.100"
# patroni_vip_mask: 24                     # default
# patroni_vip_iface: ens192                # auto-detected
# vip_manager: vip-manager         # default; set to "none" to disable the VIP layer

# --- Passwords (auto-generated when empty) ---
# postgresql_postgres_password: ""
# postgresql_replication_password: ""         # empty = TLS cert auth for replication

# --- (optional) application DBs ---
patroni_databases:
  - name: app_main
    owner: app_main
    users:
      - name: app_main
        password: "{{ vault_app_main_password }}"
        roles: [LOGIN, CREATEDB]
      - name: app_main_ro
        password: "{{ vault_app_main_ro_password }}"
        roles: [LOGIN]
        grants:
          - { privs: "CONNECT", on: "database" }
          - { privs: "SELECT", on: "all-tables" }

# --- (optional) WAL-G / S3 — enables continuous archiving + PITR ---
s3_bucket: "backups"
s3_endpoint: "https://s3.example.com"
s3_access_key: "{{ vault_s3_access_key }}"
s3_secret_key: "{{ vault_s3_secret_key }}"

# --- (optional) PostgreSQL extensions ---
# Default: [pg_stat_statements]. Extensions you add MUST be bundled in the
# Patroni image you're using — otherwise postmaster fails at bootstrap with
# "FATAL: could not access file "<ext>": No such file or directory".
# Example (requires a pgaudit-bundled image such as spilo):
#   patroni_extensions:
#     - pg_stat_statements
#     - pgaudit
```

Full variable reference: [meta/argument_specs.yml](meta/argument_specs.yml).

## Adding PostgreSQL extensions

`patroni_extensions` does two things for every extension you list:

1. Appends it to `shared_preload_libraries` (so Postgres loads the shared
   library at startup), and
2. Runs `CREATE EXTENSION IF NOT EXISTS <name>` on each database in
   `patroni_databases`.

**The library must already exist inside the container.** `pg_stat_statements`
is always bundled with Postgres core, so it works out of the box. Anything
else (`pgaudit`, `postgis`, `timescaledb`, `pg_trgm` — yes even `pg_trgm`)
needs to be baked into the image.

Three paths, cheapest first.

### Path 1 — use a pre-built image that already has what you need

Zalando's Spilo family ships with a large extension catalog (pgaudit, postgis,
pg_partman, pg_cron, timescaledb, etc.) already compiled in:

```yaml
# inventory
patroni_image: ghcr.io/zalando/spilo-16:3.2-p2

patroni_extensions:
  - pg_stat_statements
  - pgaudit
  - pg_trgm
```

No build step. Downside: Spilo is its own opinionated flavor (different init
scripts, expects env vars Patroni in this collection sets differently) — test
carefully before using in production, or stick to the default image +
extend it yourself.

### Path 2 — extend the default image with apt packages

Simplest custom route. Build once, push to your registry, point inventory at it.

```dockerfile
# Dockerfile
FROM ghcr.io/parmincloud/containers/patroni:4.1.0-pg18

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-18-pgaudit \
        postgresql-18-pg-stat-kcache \
        postgresql-18-repack \
    && rm -rf /var/lib/apt/lists/*
USER postgres
```

```bash
docker build -t registry.example.com/patroni:4.1.0-pg18-ext1 .
docker push   registry.example.com/patroni:4.1.0-pg18-ext1
```

```yaml
# inventory
patroni_image: registry.example.com/patroni:4.1.0-pg18-ext1
patroni_extensions:
  - pg_stat_statements
  - pgaudit
  - pg_stat_kcache
```

### Path 3 — compile an extension from source

Needed for extensions not packaged for your PG major (common with
cutting-edge releases like PG 18), or for pinned versions.

```dockerfile
FROM ghcr.io/parmincloud/containers/patroni:4.1.0-pg18

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential postgresql-server-dev-18 git ca-certificates \
    && git clone --depth=1 --branch v1.3.0 \
        https://github.com/pgaudit/pgaudit.git /tmp/pgaudit \
    && cd /tmp/pgaudit && make USE_PGXS=1 && make USE_PGXS=1 install \
    && apt-get purge -y --auto-remove build-essential postgresql-server-dev-18 git \
    && rm -rf /tmp/pgaudit /var/lib/apt/lists/*
USER postgres
```

Only do this when apt doesn't have what you need — the dev toolchain nearly
doubles image size even with the purge-after-build trick.

### Rolling out the new image

```bash
# after pushing the new image and updating patroni_image in inventory:
ansible-playbook playbooks/patroni/restart.yml -i inventories/<inv>.yml
```

`restart.yml` is zero-downtime — it stepsdown the leader, rolls one node at
a time, and waits for `/health` green before moving on. New `CREATE EXTENSION`
statements take effect on the next `deploy.yml` / `install.yml` run against
the same inventory.

### Dropping an extension safely

Remove it from `patroni_extensions`, then:

1. `DROP EXTENSION <name>` on each database that has it (Patroni role won't
   drop it for you — extensions often hold actual data).
2. Re-run `playbooks/patroni/restart.yml` so `shared_preload_libraries` is
   rebuilt without it.

If you skip step 1, Postgres starts fine but queries that use the extension
will error. If you skip step 2, the library stays loaded (wasted memory) but
nothing breaks.

## Architecture

### Container topology (per node)

```
client
  │
  ▼ :5432 (VIP, floats to current primary)
vip-manager ──▶ HAProxy ──▶ PgBouncer ──▶ PostgreSQL (port 55432, via Patroni)
                  │                          │
                  │                          └── replicates over TLS (:55432)
                  │
                  ▼ :5433 (read-only, round-robin over replicas)
              HAProxy replicas backend
                  │
                  ▼ :7000 (stats)
              HAProxy stats UI

etcd :2379 (clients) / :2380 (peers) — Patroni DCS
postgres_exporter :9187 (optional Prometheus target)
```

### Startup order

`etcd → patroni (boots postgres) → pgbouncer → haproxy → vip-manager`
(per the `depends_on:` chain in [templates/compose/docker-compose.yml.j2](templates/compose/docker-compose.yml.j2) and the `Requires=/After=` graph in [templates/quadlets/](templates/quadlets/))

### VIP manager choice

`vip_manager` picks how the floating IP moves between nodes on failover:

| Value | Mechanism | When to choose |
|---|---|---|
| `vip-manager` *(default)* | Watches `/service/<scope>/leader` in etcd; brings the VIP up on whichever node holds the Patroni leader key | **Preferred.** Patroni-aware — the VIP always lives with the primary. Needs etcd reachable but no multicast / VRRP on the network. |
| `keepalived` | VRRP (multicast) election between nodes; health-checks HAProxy to decide eligibility | Use when: no etcd reachability between subnets, or network admin requires VRRP. Beware: requires multicast (RFC 3768) enabled on the LAN, which many cloud / modern switch setups block. |
| `none` | No floating IP; HA left to upstream (e.g. an external L4/L7 load balancer pointing at each node) | Use when: you're running behind a cloud load balancer, a K8s service, or similar — and want Patroni to provide the DB tier while something else handles front-door addressing. |

Default is `vip-manager` because it integrates with Patroni's own leader
election semantics and doesn't require network-layer multicast.

### Scheduling verification

Backup verification is only useful if it runs periodically — a backup you
never test is a liability. Schedule from the Ansible controller via cron,
GitLab CI schedule, Ansible Tower/AWX, or any job scheduler. Reference:
[examples/crontab.example](../../examples/crontab.example).

### TLS layout

Each node gets its certs distributed to `{{ certs_dir }}` (`/opt/patroni/tls/` by default):

| Directory | Contents |
|---|---|
| `ca/` | Self-signed CA (`ca.crt`, `ca.key`) |
| `etcd/` | etcd peer + server + healthcheck certs |
| `patroni/` | Patroni REST API + PostgreSQL server + replicator certs |
| `haproxy/` | HAProxy frontend cert + `haproxy-client.pem` (cert+key bundle) |
| `vip-manager/` | vip-manager etcd client cert |

### Client connection strings

After install, per-database credential artifacts land in the controller's
`playbooks/artifacts/<inventory-stem>/patroni/<db>-<user>.txt`. Each contains:

- `postgres://...@<patroni_vip_address>:5432/...` — HAProxy r/w endpoint (leader follows)
- `postgres://...@<patroni_vip_address>:5433/...` — HAProxy r/o endpoint (replicas, round-robin)
- `postgres://...@<patroni_vip_address>:6543/...` — PgBouncer pool
- `postgres://...@<node_ips>/...?target_session_attrs=read-write` — libpq multi-host

## Day-2 operations

| Operation | Command |
|---|---|
| Cluster status | `playbooks/patroni/status.yml` |
| Deep health check | `playbooks/patroni/health.yml` |
| On-demand backup | `playbooks/patroni/backup.yml` |
| Verify latest backup | `playbooks/patroni/verify-backup.yml` |
| Point-in-time recovery (WAL-G) | `playbooks/patroni/pitr.yml -e target_time='2026-04-23 14:30:00+00'` |
| Rolling restart | `playbooks/patroni/restart.yml` |
| Controlled leader switchover | `playbooks/patroni/switchover.yml -e target_node=<host>` |
| Renew TLS certs | `playbooks/patroni/renew-certs.yml [-e rotate_ca=true]` |
| Rotate postgres + pgbouncer password | `playbooks/patroni/rotate-passwords.yml` |
| Scale up (add replica) | `playbooks/patroni/add-node.yml -e target_node=<host>` |
| Scale down (remove replica) | `playbooks/patroni/remove-node.yml -e target_node=<host>` |
| Uninstall | `playbooks/patroni/uninstall.yml [-e patroni_destroy_prune=true]` |

### Uninstall is dual-gated

There are two interactive prompts during uninstall, both expecting you to type `prune`:

1. *First prompt* (always): confirms removal of containers, configs, systemd units, and host-level cleanup.
2. *Second prompt* (only when `patroni_destroy_prune: true`): confirms deletion of the data directories (`postgresql_root_dir`, `etcd_data_dir`, etc.). Typing anything else here keeps the data on disk even though the flag was set — last-chance escape hatch before an irreversible wipe.

Use `patroni_skip_confirm: true` to bypass BOTH prompts in CI/automation. Without `patroni_destroy_prune: true`, only the first prompt fires and data is preserved.

## Variables reference

Inputs are validated by [meta/argument_specs.yml](meta/argument_specs.yml). Highlights:

| Category | Key variables |
|---|---|
| Identity | `patroni_scope`, `postgresql_version` |
| VIP | `vip_manager`, `patroni_vip_address`, `patroni_vip_mask`, `patroni_vip_iface` |
| Passwords | `postgresql_postgres_password` (auto-gen), `postgresql_replication_password` (empty = cert auth) |
| Ports | `postgresql_port` (55432), `haproxy_primary_port` (5432), `pgbouncer_port` (6543), `patroni_api_port` (8008), `etcd_client_port` (2379) |
| TLS | `tls_key_type`, `tls_key_curve`, `tls_signature_digest`, `tls_cert_days` |
| Extensions | `patroni_extensions` |
| App DBs | `patroni_databases` (list of {name, owner, users[{name, password, roles, grants}]}) |
| Backup | `patroni_backup_dir`, `wal_archive_dir`, `walg_retention` |
| S3 | `s3_bucket`, `s3_endpoint`, `s3_access_key`, `s3_secret_key` |
| Memory (auto) | `pg_shared_buffers`, `pg_effective_cache`, `pg_work_mem`, `pg_maint_mem` |

## Artifacts (controller-side)

Written to `playbooks/artifacts/<inventory-stem>/patroni/`:

- `credentials.txt` — postgres + replication + pgbouncer passwords (0600)
- `certs/` — full PKI (CA + all service certs)
- Per-DB/user credential files from `patroni_databases` (rendered by `manage_databases.yml`)

## Troubleshooting

- **`'patroni_vip_address' is undefined`** — set `patroni_vip_address` in the inventory, or set `vip_manager: none` to skip the floating-IP layer.
- **etcd quorum lost after node removal** — refuses to remove if fewer than 3 nodes would remain. Add a node before shrinking below 3.
- **WAL-G backup fails** — check S3 credentials (`s3_*`). Backup falls back to `pg_basebackup` when S3 isn't set.
- **`patronictl remove` hangs** — it's interactive; our `remove-node.yml` sets `failed_when: false` so the play continues. If etcd has stale registration, run `patronictl -c /etc/patroni/patroni.yml remove <scope>` manually on a survivor.
- **`pkg_resources` deprecation warning** — harmless; some transitive dep (WAL-G?) imports it via setuptools. Noise, not a failure.

## Gotchas

- **`patroni_scope` must be unique** per etcd cluster. If you run multiple Patroni clusters that share an etcd DCS, same scope = same cluster — they'll fight over leader election.
- **Major-version PostgreSQL upgrade is not automated** — `postgresql_version` change alone doesn't trigger `pg_upgrade`. Stop Patroni, run `pg_upgrade` manually, then restart. Minor-version upgrades (same `postgresql_version`) are fine.
- **pg_hba.conf is managed by Patroni** — if you change auth rules, do it via Patroni config and `patronictl reload`. Don't edit `pg_hba.conf` directly; Patroni overwrites it.
- **vip-manager requires Patroni API cert trust** — the vip-manager container reads `/etc/certs/ca.crt` to validate Patroni's REST API. If you rotate the CA, vip-manager must be restarted.
- **HAProxy routes ONLY when Patroni is healthy** — HAProxy's httpchk probes Patroni's `/primary` and `/replica` endpoints. If Patroni API is unreachable, HAProxy marks backends down. Check `https://<node>:8008/health` to debug.

## See also

- [roles/common/tasks/validate_inventory.yml](../common/tasks/validate_inventory.yml) — asserts Patroni inputs before install (run via `deploy.yml --tags validate`).
- [roles/mongodb](../mongodb/) — sibling NoSQL cluster role.
- [roles/netbox_lookup](../netbox_lookup/) / [roles/netbox_register](../netbox_register/) — NetBox I/O.
