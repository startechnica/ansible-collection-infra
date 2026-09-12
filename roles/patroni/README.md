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
- **Client-facing TLS terminated at PgBouncer** — HAProxy is `mode tcp` and relays the handshake through, so client TLS is negotiated with PgBouncer. `pgbouncer_client_tls_sslmode` defaults to `prefer` (TLS when the client asks, plaintext otherwise); set `require` to force it.
- **Auto-generated postgres password** when empty, persisted to controller's artifacts dir.
- **Extensions** — `pg_stat_statements` loaded by default; add others (pgaudit, postgis, timescaledb, ...) via `patroni_extensions` **only when the `patroni_image` actually bundles the corresponding shared library**.
- **Application DB/user provisioning** via `patroni_databases` (uses `community.postgresql` modules through HAProxy to the current primary).
- **Backups** — pg_basebackup (local) or WAL-G (continuous + archive to S3 or native Google Cloud Storage, selected by `walg_storage_type`).
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

# --- (optional) WAL-G — enables continuous archiving + PITR ---
# S3 backend (default): AWS S3, MinIO, Ceph, any S3-compatible gateway.
s3_bucket: "backups"
s3_endpoint: "https://s3.example.com"
s3_access_key: "{{ vault_s3_access_key }}"
s3_secret_key: "{{ vault_s3_secret_key }}"

# Native Google Cloud Storage instead — swap the block above for:
#   walg_storage_type: gcs
#   walg_gcs_bucket: "pg-backups"
#   walg_gcs_service_account_json: "{{ vault_walg_gcs_service_account_json }}"
# The credentials value is the service-account JSON key GCP hands you at
# "Create key → JSON" (raw string or parsed mapping — both work). Note that
# etcd snapshots always upload to S3, so keep the s3_* vars if you want those
# off-host too.

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
| Promote standby → primary (DR) | `playbooks/patroni/standby-promote.yml [-e patroni_skip_confirm=true]` |
| Uninstall | `playbooks/patroni/uninstall.yml [-e patroni_destroy_prune=true]` |

### Uninstall is dual-gated

There are two interactive prompts during uninstall, both expecting you to type `prune`:

1. *First prompt* (always): confirms removal of containers, configs, systemd units, and host-level cleanup.
2. *Second prompt* (only when `patroni_destroy_prune: true`): confirms deletion of the data directories (`postgresql_root_dir`, `etcd_data_dir`, etc.). Typing anything else here keeps the data on disk even though the flag was set — last-chance escape hatch before an irreversible wipe.

Use `patroni_skip_confirm: true` to bypass BOTH prompts in CI/automation. Without `patroni_destroy_prune: true`, only the first prompt fires and data is preserved.

## Standby cluster (DR / off-site replica)

A **standby cluster** is a full Patroni cluster — its own etcd, its own
`patroni_scope`, its own nodes — whose leader is a **"Standby Leader"** that
continuously replays a *remote* primary's data instead of accepting writes.
Set `patroni_standby_enabled: true` on the standby's inventory. It replicates by
**streaming** from the primary, with a **WAL-G `wal-fetch` fallback** (from the
primary's S3 archive) when the stream is unavailable. Design:
[docs/design/patroni-standby-cluster.md](../../docs/design/patroni-standby-cluster.md).

Minimum standby inventory:

```yaml
patroni_scope: myproject-pg-dr          # MUST differ from the primary's scope
patroni_standby_enabled: true
patroni_standby_primary_host: 10.0.0.10 # primary's leader IP or VIP
patroni_standby_primary_port: 55432     # primary's postgresql_port
patroni_standby_primary_slot: dr_slot   # optional: hold WAL on the primary
# TLS: pick ONE of the two trust models below
```

**Cross-cluster wiring (both ends):**

- **TLS trust** — the standby streams over TLS and must trust the primary's server cert:
  - **Shared CA (recommended):** set `patroni_shared_ca_dir` (a controller-local dir with
    `ca.key` + `ca.crt`) on **both** clusters so they chain to one CA; keep
    `patroni_standby_primary_sslmode: verify-ca`.
  - **No shared CA:** set `patroni_standby_primary_sslmode: require` (encrypted, but the
    primary's cert is not CA-verified) and set `postgresql_replication_password` on both.
- **Replication credentials** — the standby must use the **same**
  `postgresql_replication_password` (or replicator cert) as the primary.
- **Primary-side pg_hba** — add the standby node IPs to `patroni_replication_cidrs` on the
  **primary** so it admits their `replication` connections.
- **Primary-side firewall** — add the standby IPs to the primary's
  `firewall_service_source_map` for `postgresql-direct` (port 55432).
- **WAL-G fallback** — the standby must share the primary's `walg_storage_type` **and**
  bucket (`s3_bucket`, or `walg_gcs_bucket` on the GCS backend); set
  `patroni_standby_primary_walg_prefix` to the primary's `patroni_walg_s3_prefix` /
  `walg_gcs_prefix` (default `patroni-walg-<primary_scope>`) so wal-fetch reaches the
  primary's archive.
  Each cluster's own WAL-G and etcd-snapshot paths are already scope-namespaced by default
  (`patroni_walg_s3_prefix` / `walg_gcs_prefix` / `patroni_etcd_s3_prefix` =
  `patroni-walg-<scope>` / `patroni-etcd-<scope>`), so clusters sharing a bucket don't collide.

**Promotion (DR activation):** run
`playbooks/patroni/standby-promote.yml` — it removes `standby_cluster` from DCS, so Patroni
promotes the Standby Leader to a real primary. Afterward set
`patroni_standby_enabled: false` in the inventory so a later provision doesn't re-attach it.
You now have two independent primaries — fence the old one to avoid split-brain.

## Per-database pool modes

PgBouncer resolves an **exact** database name before falling back to its `*` entry, so one
pooler can serve databases that need different pool modes. `pgbouncer_database_overrides`
renders those explicit `[databases]` lines:

```yaml
pgbouncer_pool_mode: session          # global default — leave it here
pgbouncer_database_overrides:
  - { name: gitlabhq_production,    pool_mode: transaction }
  - { name: gitlabhq_production_ci, pool_mode: transaction }
```

**Keep `session` global and opt databases *into* transaction pooling, not the reverse.**
Session mode is always correct, merely less efficient. Transaction mode silently breaks
LISTEN/NOTIFY, session-level `SET`, advisory locks held across transactions, `WITH HOLD`
cursors, and temp tables. With session as the fallback, a database you forget to list costs
connections — visible. With transaction as the fallback, it corrupts behavior — not visible.
The role emits a warning if `pgbouncer_pool_mode` is `transaction` globally.

### GitLab + Praefect

These two have contradictory documented requirements — GitLab Rails
[requires](https://docs.gitlab.com/administration/postgresql/pgbouncer/) `pool_mode = transaction`,
while Praefect
[requires](https://docs.gitlab.com/administration/gitaly/praefect/configure/) session pooling
for the LISTEN/NOTIFY connection behind its read-distribution cache ("with PgBouncer this
feature is only available with `session` pool mode"). The override list resolves both from one
PgBouncer: list GitLab's databases as above and **give Praefect no entry at all** — it inherits
`session` from `*`, so its main `[database]` connection supports LISTEN and you can drop
`database.session_pooled` entirely.

Two things to get right:

- **`name` is the database name the client requests**, not a label. If GitLab connects to
  `gitlabhq_production` and you key the entry `gitlab_main`, it never matches and falls through
  to `*` — silently, at session mode. Use `dbname:` to point an alias at a differently-named
  physical database.
- **Decomposed databases need both entries.** GitLab 16+ splits `main` and `ci`; listing only
  the first leaves the CI database in session mode.

Confirm Praefect's side with its log line `reads distribution caching is enabled by
configuration` — its absence is the only signal that LISTEN isn't working.

## Variables reference

Inputs are validated by [meta/argument_specs.yml](meta/argument_specs.yml). Highlights:

| Category | Key variables |
|---|---|
| Identity | `patroni_scope`, `postgresql_version` |
| VIP | `vip_manager`, `patroni_vip_address`, `patroni_vip_mask`, `patroni_vip_iface` |
| Passwords | `postgresql_postgres_password` (auto-gen), `postgresql_replication_password` (empty = cert auth) |
| Ports | `postgresql_port` (55432), `haproxy_primary_port` (5432), `pgbouncer_port` (6543), `patroni_api_port` (8008), `etcd_client_port` (2379) |
| TLS | `tls_key_type`, `tls_key_curve`, `tls_signature_digest`, `tls_cert_days` |
| Client TLS | `pgbouncer_client_tls_sslmode` (`prefer`; `require` forces TLS), `pgbouncer_client_tls_protocols`, `pgbouncer_client_tls_ciphers` |
| Pooling | `pgbouncer_pool_mode` (`session`), `pgbouncer_database_overrides` (per-database pool modes) |
| HAProxy timeouts | `haproxy_tunnel_timeout` (24h — governs established sessions), `haproxy_client_timeout` / `haproxy_server_timeout` (300s — backstop; superseded by `tunnel` on the pg listeners), `haproxy_connect_timeout` (bounds backend connection setup), `haproxy_client_fin_timeout` / `haproxy_server_fin_timeout` (override `tunnel` for half-closed connections) |
| Extensions | `patroni_extensions` |
| App DBs | `patroni_databases` (list of {name, owner, users[{name, password, roles, grants}]}) |
| Backup | `patroni_backup_dir`, `wal_archive_dir`, `walg_retention`, `walg_storage_type` (`s3`\|`gcs`) |
| S3 | `s3_bucket`, `s3_endpoint`, `s3_access_key`, `s3_secret_key`, `patroni_walg_s3_prefix` |
| GCS | `walg_gcs_bucket`, `walg_gcs_service_account_json` (vault the service-account key), `walg_gcs_prefix` |
| Memory (auto) | `pg_shared_buffers`, `pg_effective_cache`, `pg_work_mem`, `pg_maint_mem` |

## Artifacts (controller-side)

Written to `playbooks/artifacts/<inventory-stem>/patroni/`:

- `credentials.txt` — postgres + replication + pgbouncer passwords (0600)
- `certs/` — full PKI (CA + all service certs)
- Per-DB/user credential files from `patroni_databases` (rendered by `manage_databases.yml`)

## Troubleshooting

- **`'patroni_vip_address' is undefined`** — set `patroni_vip_address` in the inventory, or set `vip_manager: none` to skip the floating-IP layer.
- **etcd quorum lost after node removal** — refuses to remove if fewer than 3 nodes would remain. Add a node before shrinking below 3.
- **WAL-G backup fails** — check the credentials for the active backend: `s3_*` on `walg_storage_type: s3`, `walg_gcs_service_account_json` on `gcs`. Backup falls back to `pg_basebackup` when no bucket is set.
- **WAL-G on GCS fails with a credentials error** — the service-account key is mounted read-only at `/etc/walg/credentials.json` inside the patroni container. Verify with `podman exec patroni cat /etc/walg/credentials.json` (empty/missing means the host file at `/opt/walg/gcs/credentials.json` isn't patroni-readable), and confirm the service account has `roles/storage.objectAdmin` on `walg_gcs_bucket` — wal-g needs list, read, write, **and** delete (retention prunes old backups).
- **`server does not support SSL` / `SSL is not enabled on the server`** — the client's TLS request reached PgBouncer while `pgbouncer_client_tls_sslmode` was `disable`. Every published connection string uses `sslmode=verify-ca`, and HAProxy (`mode tcp`) relays the handshake straight through to PgBouncer, so client TLS lives or dies on that setting. Default is `prefer`; check the rendered `/opt/pgbouncer/pgbouncer.ini`.
- **Client TLS fails with `verify-full`** — the pgbouncer leaf's SANs cover node IPs, hostnames, and localhost but **not** `patroni_vip_address`, so a client connecting through the VIP can't match a hostname. Use `sslmode=verify-ca` (validates the CA chain, no hostname check) — what the credential artifacts already publish — or add the VIP to the SAN list in `tls_certificates.yml` and reissue.
- **LISTEN/NOTIFY clients silently miss notifications** — check `haproxy_tunnel_timeout`. HAProxy's `timeout client`/`timeout server` are *inactivity* timers and an idle listener trips them; `timeout tunnel` is what keeps established sessions alive. TCP keepalives do **not** reset those timers, so `option clitcpka` being present proves nothing here. Note also that LISTEN/NOTIFY only works with `pgbouncer_pool_mode: session` (the default) — under `transaction` the server connection returns to the pool between transactions and notifications land on whichever client holds it next.
- **Long queries die at exactly 5 minutes** — same cause: a statement that sends no bytes while running (`CREATE INDEX`, big analytical query, `pg_dump` through the proxy) trips `haproxy_client_timeout`/`haproxy_server_timeout` if `timeout tunnel` isn't in effect. PgBouncer won't be the culprit — `query_timeout` is `0` by design.
- **`patronictl remove` hangs** — it's interactive; our `remove-node.yml` sets `failed_when: false` so the play continues. If etcd has stale registration, run `patronictl -c /etc/patroni/patroni.yml remove <scope>` manually on a survivor.
- **`pkg_resources` deprecation warning** — harmless; some transitive dep (WAL-G?) imports it via setuptools. Noise, not a failure.
- **Standby cluster not catching up** — check, in order: (1) TLS trust — with `sslmode: verify-ca` the standby needs the primary's CA (set `patroni_shared_ca_dir` on both, or relax to `patroni_standby_primary_sslmode: require`); (2) the primary admits the standby IPs — `patroni_replication_cidrs` on the primary + its firewall for port 55432; (3) matching `postgresql_replication_password`; (4) `patronictl list` on the standby should show a `Standby Leader` — if leader election fails outright, the scope likely collides with the primary's.

## Gotchas

- **`patroni_scope` must be unique** per etcd cluster. If you run multiple Patroni clusters that share an etcd DCS, same scope = same cluster — they'll fight over leader election. A **standby cluster** especially must set a `patroni_scope` distinct from its primary — the role asserts this (rejects the default placeholder) when `patroni_standby_enabled: true`.
- **Major-version PostgreSQL upgrade is not automated** — `postgresql_version` change alone doesn't trigger `pg_upgrade`. Stop Patroni, run `pg_upgrade` manually, then restart. Minor-version upgrades (same `postgresql_version`) are fine.
- **pg_hba.conf is managed by Patroni** — if you change auth rules, do it via Patroni config and `patronictl reload`. Don't edit `pg_hba.conf` directly; Patroni overwrites it.
- **vip-manager requires Patroni API cert trust** — the vip-manager container reads `/etc/certs/ca.crt` to validate Patroni's REST API. If you rotate the CA, vip-manager must be restarted.
- **HAProxy routes ONLY when Patroni is healthy** — HAProxy's httpchk probes Patroni's `/primary` and `/replica` endpoints. If Patroni API is unreachable, HAProxy marks backends down. Check `https://<node>:8008/health` to debug.

## See also

- [roles/common/tasks/validate_inventory.yml](../common/tasks/validate_inventory.yml) — asserts Patroni inputs before install (run via `deploy.yml --tags validate`).
- [roles/mongodb](../mongodb/) — sibling NoSQL cluster role.
- [roles/netbox_lookup](../netbox_lookup/) / [roles/netbox_register](../netbox_register/) — NetBox I/O.
