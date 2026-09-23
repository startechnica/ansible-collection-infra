# Changelog

All notable changes to this collection are documented in this file. The format
is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this collection adheres to [Semantic Versioning](https://semver.org/).

## 1.0.3 (unreleased)

### Breaking changes

- **Every variable in the `mongodb` and `patroni` roles is now role-prefixed.**
  133 variables renamed — 22 in `mongodb`, 111 in `patroni` — with **no
  aliases**. An inventory using an old name is silently ignored and the role's
  default applies. Full tables and a migration `sed` per role:
  [docs/UPGRADING.md](docs/UPGRADING.md).

  The transformation is mechanical (prepend the role name), so whole families
  move at once:

  | Old prefix | New prefix |
  | --- | --- |
  | `mongod_*`, `configsvr_*`, `mongos_*` | `mongodb_mongod_*`, `mongodb_configsvr_*`, `mongodb_mongos_*` |
  | `postgresql_*`, `pg_*` | `patroni_postgresql_*`, `patroni_pg_*` |
  | `etcd_*`, `haproxy_*`, `pgbouncer_*`, `walg_*` | `patroni_etcd_*`, `patroni_haproxy_*`, `patroni_pgbouncer_*`, `patroni_walg_*` |
  | `postgres_exporter_*`, `vip_manager_*`, `keepalived_*` | `patroni_postgres_exporter_*`, `patroni_vip_manager_*`, `patroni_keepalived_*` |
  | `s3_*`, `tls_*` | `patroni_s3_*` / `mongodb_s3_*`, `patroni_tls_*` / `mongodb_tls_*` |

  Six are not a plain prefix:

  | Old name | New name |
  | --- | --- |
  | `ssl_days`, `ssl_ca_days` | `mongodb_tls_days`, `mongodb_tls_ca_days` |
  | `certs_dir` | `patroni_tls_dir` (a plain prefix would collide with `patroni_certs_dir`, patroni's own leaf-cert directory) |
  | `vip_manager` | `patroni_vip_engine` |
  | `debug`, `dry_run` | `mongodb_debug` / `patroni_debug` / `patroni_dry_run`, each defaulting to the collection-wide value so `-e debug=true` still reaches them |

  **The `s3_*` and `tls_key_*` renames fail quietly.** Those names are still
  valid — they belong to `patroni` now — so a dual-stack inventory that set them
  once for both roles keeps validating and keeps running while mongodb stops
  seeing them. `mongodb_s3_bucket` is empty, `mongodb_backup_mode` falls back to
  `local`, and **scheduled MongoDB backups keep succeeding but stop being
  uploaded to S3.** Add a `mongodb_s3_*` set (it may name the same bucket)
  before the next run. If migrating both roles, do mongodb first: `s3_*` becomes
  patroni's, so copy those values out before renaming them away.

  **Why:** `tls_key_curve` was defined by both roles with *different* values
  (`secp256r1` vs `secp384r1`) and both feed certificate generation, so
  whichever role's defaults loaded last silently chose the curve for the other's
  certificates. The two roles now share no variable name at all — 90 and 165
  defaults, zero intersection — which `tests/preflight_mem_budget.yml` asserts in
  both directions, alongside a per-role check that neither has an unprefixed
  default. A collection-wide name is consumed by declaring a prefixed variable
  that falls back to it, never by declaring the bare name.

- **Removed `s3_retain_days`.** Remote (S3) backup retention now always follows
  `mongodb_backup_retain_days` — the var was a needless second knob (its default
  was already `{{ mongodb_backup_retain_days }}`). Only affects deployments that
  set it to a value *different* from `mongodb_backup_retain_days`.

- **Renamed `s3_prefix` → `mongodb_backup_s3_prefix`** and changed its default
  from `mongodb/{{ mongodb_network }}` to a cluster-namespaced, slugified path
  `mongodb-{{ mongodb_cluster_name | slugify }}`. This **moves the default S3
  backup path** (mongodump + PBM under `<prefix>/pbm`); existing objects are not
  migrated. To keep the previous location, set
  `mongodb_backup_s3_prefix: "mongodb/{{ mongodb_network }}"`.

- **Changed defaults.** Each of these alters a running deployment on its next
  run:

  | Variable | 1.0.2 | 1.0.3 |
  | --- | --- | --- |
  | `mongodb_version` | `8.2.7` | `8.0.28` (LTS; rapid releases are unsupported by PBM 2.15) |
  | `mongodb_exporter_image` | `percona/mongodb_exporter:0.51.0` | `:0.52.0` |
  | `mongodb_mongos_mem_limit_mb` | `512` | `1024` |
  | `patroni_pgbouncer_default_pool_size` | `20`, and **inert** | `50` |

  The pool size needs explaining: `templates/pgbouncer.ini.j2` hardcoded
  `default_pool_size = 50` and never read the variable, so 50 is what every
  deployment has always run. The new default preserves that. Wiring the old name
  up as-is would instead have cut anyone who set it from 50 to 20.

### Added

- **`deploy.yml` Stage 7.5 applies `grafana_alloy`** on `cluster_nodes` where
  `grafana_alloy_enabled=true`, after the data services and the firewall.
  `--tags alloy` (or `make deploy-alloy INV=<inventory>`) re-applies only the
  collector. Previously no playbook included the role.
- **`grafana_alloy` can ship a database VM's full telemetry.** All new options
  are off by default, and with none of them set `config.alloy`, the Quadlet and
  the compose file render byte-for-byte as before (golden files in
  `tests/fixtures/grafana_alloy/`).
  - `grafana_alloy_host_metrics_module_enabled` takes host metrics from the
    `startechnica/grafana` `host_metrics.unix` module (`import.git`) instead of
    the built-in unix exporter, so VMs and Kubernetes nodes running the module
    produce the same series: job `integrations/unix`, `instance` and `node` =
    host name. The module owns the remote_write; tenant, credentials and
    external labels are passed to it. It sends no `cluster_name` unless
    `grafana_alloy_external_labels` has one. The container gets the host `/` at
    `/host/root` (`ro,rslave`) and `--pid=host`, as the module requires.
    Repository, revision, path and pull frequency are variables.
  - `grafana_alloy_prometheus_tenant_id` sets `X-Scope-OrgID` on the metrics
    remote_write (the counterpart of `grafana_alloy_loki_tenant_id`).
  - `grafana_alloy_external_labels` adds labels (e.g. `region`, `zone`) to every
    metric and every log line.
  - `grafana_alloy_extra_scrapes` adds local Prometheus scrapes (`job`,
    `targets`, `scheme`, `tls_insecure_skip_verify`, `metrics_path`,
    `scrape_interval`, `metric_drop_regex`, static `labels`, `name`). Every one
    sets `instance` to the host name, never `127.0.0.1:<port>`, so exporter
    series join the host metrics on `instance`. Static `labels` tell apart
    several targets that share a job and instance, e.g. three MongoDB exporters.
  - `grafana_alloy_self_metrics_enabled` scrapes Alloy's own `/metrics` as job
    `integrations/alloy`, so collector-health alerts cover the host.
  - `tasks/validate_metrics.yml` checks these inputs on provision (label names,
    unique component names, tenant with the module), so a mistake fails the
    play instead of Alloy's config load on the host.
- **`patroni_etcd_listen_metrics_urls`**: a plain-HTTP listener for etcd's
  `/metrics` and `/health`, e.g. `http://127.0.0.1:2381`. The client port
  requires a client certificate, so this is how a local scraper reads etcd.
  Empty (default) leaves the etcd command unchanged. Setting it recreates etcd,
  so roll it out one member at a time.
- **Per-member MongoDB exporters** for sharded clusters:
  `mongodb_exporter_shard_enabled` (`:9217`, the shard mongod) and
  `mongodb_exporter_configsvr_enabled` (`:9218`, the config server), each
  connected directly to its process instead of `mongos`, which reports router
  state only. Off by default. The role creates the exporter's X.509 user on the
  shard replica set, where a direct connection can see it
  (`tasks/exporter_users.yml`, member-cert auth like `pbm_setup.yml`). While
  they are on, every exporter mounts `exporter.pem` with the shared SELinux
  label (`z`): a private label (`Z`) is rewritten by whichever container starts
  last and locks the others out. The preflight memory budget counts them. Not
  `--discovering-mode`: that auto-discovers collections, not cluster members.
- **The Patroni stack's HAProxy `/metrics` is regression-tested**
  (`tests/patroni_metrics_endpoints.yml`). The stats listener's
  `use-service prometheus-exporter` rule has been in the template since
  2026-09-13 and routes `/metrics` to the exporter even under `stats uri /`,
  because HAProxy runs `http-request` rules before it matches the stats URI.
  A host whose `/metrics` still returns the HTML stats page has a `haproxy.cfg`
  rendered before that date; re-running the role fixes it.
- **etcd snapshots upload to GCS.** On clusters where WAL-G uses the native
  GCS backend (`patroni_walg_storage_type: gcs`), each snapshot now also goes to
  `patroni_walg_gcs_bucket` under `patroni_etcd_gcs_prefix` (default
  `patroni-etcd-<scope>`), in one folder per host because every member takes
  its snapshot at the same moment. Before this, snapshots only left the host on S3.
  The upload runs `wal-g st put` in a throwaway container from
  `patroni_walg_image` with WAL-G's service-account key, so nothing new is
  installed. S3 upload is unchanged and still runs whenever `patroni_s3_bucket`
  is set. Remote retention is left to a bucket lifecycle rule.
- **Percona Backup for MongoDB (PBM)** — cluster-consistent backups and PITR for
  **sharded** clusters, where `mongodump --oplog` cannot run through a `mongos`.
  Opt in with `mongodb_backup_pbm_enabled`; one agent runs beside every
  data-bearing mongod (two per host when sharded). Agents authenticate by X.509
  client cert. Storage backend is selectable with `mongodb_backup_storage_type`
  (`minio` default, `s3`, `gcs`) — `minio` avoids an AWS SDK Go v2 signing header
  some proxies rewrite into `SignatureDoesNotMatch`, and path-style addressing is
  the default so S3-compatible gateways work. Continuous oplog slicing follows
  the shared `mongodb_backup_pitr_enabled` switch; `mongodb_backup_init` lays
  down the first base backup so PITR can start. Compression is configurable
  (`mongodb_backup_compression_type`/`_level`, `zstd` level 3 by default).
  Preflight rejects a MongoDB rapid release, which PBM 2.15 does not support.
  On the Community `mongo` image PBM does logical backups and logical PITR only.
  `mongodb_container_engine_bin` pins the absolute engine path the scheduled
  backup unit's `ExecStart=` needs, since systemd units have no `PATH` lookup;
  empty (the default) resolves it per host with `command -v`.
  Design notes: [docs/design/mongodb-pbm.md](docs/design/mongodb-pbm.md).
- **Patroni standby clusters** (DR / off-site replica) — `patroni_standby_enabled`
  plus `patroni_standby_primary_{host,port,slot,sslmode,walg_prefix}` and
  `patroni_standby_create_replica_methods`. The cluster's leader replays a remote
  primary by streaming with a WAL-G `wal-fetch` fallback instead of accepting
  writes. `playbooks/patroni/standby-promote.yml` removes the DCS
  `standby_cluster` block to promote. `patroni_shared_ca_dir` lets primary and
  standby share one CA so `verify-ca` streaming trusts the primary's cert, and
  `patroni_replication_cidrs` admits the standby's IPs on the primary. Design
  notes: [docs/design/patroni-standby-cluster.md](docs/design/patroni-standby-cluster.md).
- **Native GCS backends.** WAL-G: `patroni_walg_storage_type: gcs` with
  `patroni_walg_gcs_bucket` / `_prefix` and a service-account key in
  `patroni_walg_gcs_service_account_json` (vault it — it is a private key). PBM:
  `mongodb_backup_storage_type: gcs` with `mongodb_backup_gcs_bucket` / `_prefix`
  / `_service_account`. Both use service-account keys rather than HMAC, which
  PBM 2.12+ deprecates.
- **Preflight memory budget.** `roles/preflight/tasks/memory.yml` reports what a
  host is about to commit against total RAM, warns past a headroom ceiling
  (`container_mem_headroom_pct`, 15%) and fails past total RAM under
  `container_mem_strict`. `container_mem_reserved_mb` models a second stack on
  the same node, which is how a co-located MongoDB + Patroni node gets a true
  picture: the `mongodb` role imports patroni's own `patroni_mem_request_mb`
  through a narrow `export_vars` import rather than asking inventory to restate
  it. Patroni's request is `patroni_pg_shared_buffers` plus a flat
  `patroni_stack_overhead_mb` (640) for etcd, HAProxy, PgBouncer, the VIP manager
  and the exporter; mongodb's is the sum of every container cap it places,
  including the PBM agents and the exporter (`mongodb_exporter_mem_limit_mb`,
  `mongodb_backup_pbm_mem_limit_mb`). `mongodb_mem_reserved_mb` /
  `patroni_mem_reserved_mb` remain as additive escape hatches for memory the
  collection cannot introspect. Regression coverage:
  `tests/preflight_mem_budget.yml`.
- **Preflight PostgreSQL connection budget.** `roles/preflight/tasks/pg_connections.yml`
  reports `(pool + reserve) x pairs + bypass + overhead` against
  `patroni_pg_max_connections` on every run, warns when it overflows, and fails
  under `patroni_pg_conn_strict`. PgBouncer's pool sizes are **per (user,
  database) pair**, so the demand is a product — adding one application database
  silently adds 60 real backends at the shipped defaults. The pair count derives
  from `patroni_databases` plus PgBouncer's own `auth_query` pool.
  `patroni_pg_conn_overhead` (20) accounts for `superuser_reserved_connections`,
  Patroni's monitoring connection, the exporter, WAL-G and admin sessions.
  Note that at the shipped defaults the budget runs out at the **third** pair
  (`3 x 60 + 20 bypass + 20 overhead = 220` against 200), so a cluster with one
  database and two users already warns. Such clusters work in practice — pools
  fill on demand and are rarely all saturated — and the defaults are deliberately
  left at today's effective values rather than tuned to silence it. Regression
  coverage: `tests/pg_connection_budget.yml`.
- **`patroni_pg_max_connections`, reconciled post-bootstrap.** PostgreSQL's
  `max_connections` was a literal `200` in `templates/patroni.yml.j2`, so the
  ceiling every connection cap was reasoned against could not be read or raised
  from inventory. `bootstrap.dcs` only seeds a cluster at first init, so
  `shared/reconcile_pg_parameters.yml` pushes changes to DCS via `patronictl
  edit-config` — the same bridge `reconcile_archiving.yml` provides for
  `archive_command`. It is a postmaster parameter, so a change leaves members in
  `pending_restart`: the role reports the restart order (replicas first; leader
  first when *lowering*, since a replica below the primary's value refuses to
  start) and never restarts anything itself. The inventory value wins over an
  out-of-band `edit-config`.
- **OOM victim selection across every `mongodb` and `patroni` container.** Each
  unit sets `oom_score_adj` on both the Quadlet and compose paths, so when the
  **host** (not a cgroup) runs out of memory the kernel kills something cheap
  instead of the largest process. Ranked: `patroni_etcd_oom_score_adj` (-900),
  `patroni_oom_score_adj` (-500), `patroni_keepalived_oom_score_adj` /
  `patroni_vip_manager_oom_score_adj` (-500), `patroni_haproxy_oom_score_adj` /
  `patroni_pgbouncer_oom_score_adj` (-300), `mongodb_mongod_oom_score_adj` /
  `mongodb_configsvr_oom_score_adj` (0), `mongodb_mongos_oom_score_adj` (500),
  `mongodb_backup_pbm_oom_score_adj` (800), the exporters (1000). Also
  `patroni_pg_backend_oom_adjust_enabled` / `patroni_pg_backend_oom_score_adj`,
  which set `PG_OOM_ADJUST_FILE`/`_VALUE` so the postmaster survives and a
  backend is killed instead. This matters most without swap: FCOS never has any,
  so the kernel goes straight from "full" to an OOM kill. Regression coverage:
  `tests/oom_score_adj.yml`.
- **HAProxy pooler-bypass listener and configurable caps.** A third listener,
  `pg-direct` (`patroni_haproxy_direct_port`, 5434), reaches the leader's
  PostgreSQL directly for migrations and admin tooling. Per-listener frontend and
  per-server caps are variables
  (`patroni_haproxy_{primary,replicas,direct}_maxconn` and `_server_maxconn`,
  plus `patroni_haproxy_global_maxconn`); `pg-direct` is held ~50x smaller than
  the pooled listeners because every connection on it is a real backend
  competing for `max_connections`. All six timeouts are variables too
  (`patroni_haproxy_{client,server,connect,tunnel,client_fin,server_fin}_timeout`)
  and health-check tuning is exposed as `patroni_haproxy_check_{inter,fall,rise,timeout}`.
- **PgBouncer client TLS, per-database pool modes, and pool sizing.**
  `patroni_pgbouncer_client_tls_sslmode` (default `prefer`, non-breaking) with
  `_protocols` and `_ciphers` — PgBouncer terminates client TLS for the whole
  stack, since HAProxy runs `mode tcp` and relays the handshake through.
  `patroni_pgbouncer_database_overrides` renders explicit `[databases]` entries
  so one pooler can serve databases needing different pool modes; the motivating
  case is GitLab, where Rails requires transaction pooling while Praefect
  requires session pooling for its LISTEN/NOTIFY connection. Pool sizing is
  configurable: `patroni_pgbouncer_max_client_conn` (1000),
  `_default_pool_size` (50), `_reserve_pool_size` (10), `_min_pool_size` (10) —
  all four were hardcoded in the template.
- **MongoDB scheduled backups and a backup engine selector.** Provisioning
  installs a host-level systemd timer (`mongodb-backup.timer`) on one node
  running the same dump → prune → upload flow on `mongodb_backup_schedule`
  (daily 02:00), with `Persistent=true` to catch up a missed run; no crond
  dependency, matching patroni's `walg-cron.timer`. `mongodb_backup_enabled`
  tears it down. `mongodb_backup_type` selects `mongodump` or `pbm`, following
  `mongodb_backup_pbm_enabled` by default. `mongodb_backup_mode` selects `local`
  or `s3`, defaulting to `s3` when a bucket is configured. `pitr.yml` gained
  `backup_source: s3`, and `mongodb_backup` gained `oplog` and TLS parameters.
- **Per-cluster S3 prefixes for Patroni backups.** WAL-G and etcd snapshots wrote
  to bucket-root paths, so two clusters sharing a bucket collided.
  `patroni_walg_s3_prefix` and `patroni_etcd_s3_prefix` default to
  cluster-namespaced paths derived from `patroni_scope`.
- **`export_vars` anchors on `common`, `instance`, `mongodb` and `patroni`.** A
  task-free entry point that loads a role's variables into the calling play
  without running it, so a consumer reads the same values the deploy used instead
  of duplicating defaults. `defaults_from` / `vars_from` narrow the import to one
  topic file; `vars/empty.yml` skips computed vars entirely. `instance_ignition`
  and `instance_cloud_init` are standalone roles usable without the `instance`
  orchestrator.
- **Two new preflight checks.** The container-engine binary is verified on PATH
  before engine-specific checks, so a missing CLI reports itself rather than
  surfacing as "Compose plugin not found". On hosts in `patroni_nodes`, the VIP
  settings (`patroni_vip_address`, `patroni_vip_mask`, `patroni_vip_engine`) are
  validated by importing only patroni's VIP defaults.
- **`butane` is downloaded automatically.** Ignition presets no longer need a
  pre-installed `butane`: one on `PATH` is used, otherwise the pinned release is
  fetched (sha256-verified) into `~/.cache/startechnica/butane/`. Air-gapped
  controllers still need it on `PATH` or a mirror in
  `ignition_butane_release_url`.
- **Image pulls are retried.** A large pull failing on one transient network
  error no longer fails the run.
- **Content-library OVA import is retried**, and **DVS NIC binding is verified
  after provision** by a post-configure assert.

### Changed

- **Both roles' defaults and vars are split into one file per topic.**
  `roles/patroni/defaults/main.yml` → `defaults/main/*.yml` (14 files) and
  `roles/mongodb/defaults/main.yml` → `defaults/main/*.yml` (12 files), with
  `vars/main.yml` split the same way in both. This is what lets a consumer import
  one slice with `defaults_from` instead of every variable the role has. Each
  role's `main/resources.yml` (mongodb) and `main/postgresql.yml` (patroni) is
  deliberately self-contained so the memory budget is importable from a single
  file.
- **The MongoDB kernel-compatibility gate now applies to every version.** The
  vendored-TCMalloc/rseq incompatibility with Linux `>= 6.19` is open-ended and
  affects the 8.0 LTS line including `8.0.28`, so the gate is independent of
  `mongodb_version`. Preflight fails before starting containers rather than
  letting mongod crash-loop into a misleading port-listener timeout. The former
  version-gated `mongodb_kernel_guard_min_version` and `mongodb_fixed_kernel` are
  retained but **inert** for inventory compatibility. Resolution is a kernel
  `< 6.19` or a non-affected OS; choosing an older MongoDB version is not one.
- **Patroni HAProxy servers are named by node hostname.** The `server` lines
  carried positional names, which surfaced in the stats page and the Prometheus
  `server` label; addresses are unchanged.
- **`grafana_alloy`'s engine fallback follows `instance_platform_preset`**
  through `common`'s resolved engine rather than defaulting to podman
  independently.
- **`deploy.yml` registers VMs in NetBox in its own play** (Stage 2.1).
- **`instance` no longer copies `instance_folder_path` and `ignition_tmp_dir`**
  into every host's vars.
- **Raised minimum `netbox.netbox` to `>=3.23.0`** and `community.mongodb` to a
  newer floor.

### Fixed

- **`grafana_alloy`: a password that references an undefined variable now
  fails the play.** `grafana_alloy_env_has_secrets` reads each password through
  `default('')`, which also swallowed an unresolvable reference such as
  `"{{ vault_x }}"` with the vault file not loaded. The role then removed
  `alloy.env` and Alloy sent an empty password, which surfaced only as rejected
  writes. Provision now resolves each password first and fails, naming the
  missing variable.
- **etcd snapshots could fill the host disk, and a failed snapshot looked
  like a success.** Clusters deployed with older versions of the role write
  each snapshot to the etcd container's `/tmp` and then run `exec etcd rm`.
  The etcd image is distroless, so the `rm` failed on every run, with the
  error sent to `/dev/null`. The copies grew with the etcd database until a
  production node's disk filled (220 GB). The current script staged snapshots
  in etcd's own data directory instead. It also exited 0 when etcdctl failed,
  so a timer that never produced a snapshot still looked healthy. Now
  `patroni_etcd_snapshot_dir` is bind-mounted into etcd at
  `patroni_etcd_snapshot_mount` (`/etcd-snapshots`) and owned by etcd's user,
  so etcdctl writes each snapshot straight to its final place. Rotation (the
  newest `patroni_etcd_snapshot_retain`), cleanup of interrupted `.part`
  files and of the old staging files, and uploads all run on the host. The
  script exits non-zero when the snapshot or an upload fails. The unit
  (`etcd-snapshot.service`, now a template) orders after `etcd.service` on
  podman and `docker.service` on docker. It previously ordered after
  `patroni.service`, which doesn't exist on docker. The mount recreates etcd;
  see [docs/UPGRADING.md](docs/UPGRADING.md). Regression coverage:
  `tests/etcd_snapshot.yml`, which runs the rendered script against a
  stand-in engine and checks what it leaves on disk.
- **Patroni clusters lost the ability to fail over after about four months.**
  etcd ran without `--auto-compaction-*`, so it kept every version of the
  member, status and leader keys Patroni rewrites every 10 s from each node.
  The backend grew by roughly 18 MB a day until it reached etcd's default 2 GB
  quota. etcd then raised a `NOSPACE` alarm and rejected every write. The
  running primary only renews a lease, so it stayed up and nothing looked
  wrong. When the primary next failed, no replica could write the leader key
  and the cluster was left with no primary. This happened on a production
  cluster 127 days after it was built. etcd now compacts to one hour of history
  by default: `patroni_etcd_auto_compaction_mode` (`periodic`) and
  `patroni_etcd_auto_compaction_retention` (`1h`), rendered on both the
  compose and Quadlet paths. Set the mode to `""` to leave compaction off.
  Existing clusters pick it up by recreating etcd one member at a time. A
  cluster that is already in `NOSPACE` also needs a one-off compact, defrag and
  disarm. Both steps are in [docs/UPGRADING.md](docs/UPGRADING.md) and
  [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md). Regression coverage:
  `tests/etcd_compaction.yml`.
- **Scheduled WAL-G backups never ran on docker hosts.** `walg-cron.service`
  hard-coded `Requires=patroni.service`, and that unit only exists where Quadlet
  generates it (podman). On docker, systemd refused to start the backup
  (`Unit patroni.service not found`) every night while the timer stayed active,
  so the only sign was in the journal. The unit is now a template that depends
  on `patroni.service` on podman and `docker.service` on docker. Regression
  coverage: `tests/walg_cron_unit.yml`.
- **HAProxy dropped idle LISTEN/NOTIFY sessions and long-running statements
  after 5 minutes.** `timeout client` / `timeout server` were hardcoded to
  `300s`, and in `mode tcp` those are *inactivity* timers. A LISTEN/NOTIFY client
  is idle by design, so it was disconnected every 5 minutes of quiet — and
  because NOTIFY is not queued for absent listeners, every notification
  published during the gap was lost with no error until the client next touched
  the socket. The same timer cut any statement running longer than 300s without
  sending bytes (`CREATE INDEX`, analytical queries, `pg_dump` through the
  proxy), which PgBouncer deliberately does not cap (`query_timeout = 0`). The
  existing `option clitcpka` / `srvtcpka` keepalives did not help: a keepalive
  probe carries no application data, so it does not reset the inactivity timers.
  Adds `timeout tunnel` (24h), which in `mode tcp` supersedes client/server once
  a connection is established — setup stays bounded by the unchanged 300s while
  established sessions get a long leash, which is safe precisely because the
  keepalives reap dead peers in ~2 min. Regression coverage:
  `tests/haproxy_timeouts.yml`.
- **Patroni replicas logged `no pg_hba.conf entry for replication ... 127.0.0.1`
  on every HA cycle.** Patroni opens a replication connection to its own node
  over loopback, and `all` pg_hba rules never match replication connections.
  Adds loopback `replication replicator` rules (`scram-sha-256` when
  `patroni_postgresql_replication_password` is set, `cert` otherwise) plus `::1`
  twins of the local `all` rules. These live in `bootstrap.pg_hba`, so they apply
  only to newly bootstrapped clusters — add them by hand on an existing cluster.
- **Patroni's rendered `docker-compose.yml` was invalid YAML whenever WAL-G was
  enabled** (docker engine only). The patroni service's `depends_on` mixed the
  short list form with the `walg-init` mapping entry carrying
  `condition: service_completed_successfully` — a block sequence and a block
  mapping at the same level, which no YAML parser accepts, so `docker compose up`
  refused the file. Both entries now use the long mapping form. The podman
  (Quadlet) path was never affected.
- **`mongodump --oplog` against a sharded cluster is a dead flag** — accepted and
  silently ignored, producing a backup that looks PITR-capable and is not. Now
  caught at preflight.
- **MongoDB day-2 backup playbooks were docker-only.** `pitr.yml` and
  `verify-backup.yml` assumed the docker engine and failed on podman hosts.
- **etcd snapshot cleanup failed on the distroless etcd image**, which has no
  shell for the cleanup step.
- **Backups passed `--tls` for `preferTLS`**, breaking on older `mongodump`.
- **S3 credential injection broke on keys starting with a digit.**
- **Transient `IncompleteRead` on the post-rebind NIC re-probe** failed provision
  runs.
- **Distributed-portgroup NICs deployed disconnected** — content-library OVFs
  bind NICs at import time and needed an explicit reconnect.
- **FCOS auto-import never ran**, dead since it was added: the gate required a
  variable that was never set on that path.
- **FCOS metadata fetch built `streams/.json`** (a 404) when the preset was
  unset.
- **FCOS layered `python3` on provision-only VMs** that never run a service role.

## 1.0.2 (2026-06-08)

### Breaking changes
- **`netbox_device_platform` default changed from `ubuntu-24-04-lts` to `""`.**
  When empty it now falls back to `instance_platform_preset`, so a deployment
  with no explicit `netbox_device_platform` set will register the VM with
  `instance_platform_preset` (default `fedora-coreos`) instead of
  `ubuntu-24-04-lts`. Set `netbox_device_platform: ubuntu-24-04-lts` explicitly
  to preserve the previous value. Migration: [docs/UPGRADING.md](docs/UPGRADING.md).

### Added
- **`grafana_alloy` role** — deploys [Grafana Alloy](https://grafana.com/docs/alloy/)
  as a single container per host to ship logs to Loki (systemd journal),
  metrics to Prometheus (`remote_write`), and traces to Tempo (OTLP receiver →
  exporter).
- `netbox_register_create` (default `true`) — the `netbox_register` role now
  auto-creates the **platform, device role, and cluster** a VM references when
  the slug/name doesn't already exist in NetBox, instead of failing
  registration. Look-up-then-create only: existing objects are never modified.
  Two supporting vars: `netbox_cluster_type` (default `VMware vSphere`, used
  when a cluster must be created — NetBox requires a type) and
  `netbox_register_default_role_color` (default `9e9e9e`). Site and tenant are
  intentionally NOT auto-created (org-authoritative). Set
  `netbox_register_create: false` to require all referents to pre-exist.
- Post-write verification in `roles/netbox_register/tasks/register_services.yml`.
  After the per-VM service writes complete, re-queries NetBox for the
  VM's services and asserts every catalog entry's `name` is present.
  Catches server-side acceptance without persistence, deletions during
  the write loop, and silent misregistration. One extra GET per VM.
- `.claude/settings.json` with `permissions.allow: ["Bash"]` for the
  repo. Project-scoped: applies to anyone who clones and runs Claude
  Code here.

### Changed
- `netbox_device_platform` now falls back to `instance_platform_preset` when
  left empty, so a VM's registered NetBox platform matches the OS preset it was
  actually built from. Resolution order: per-VM `item.platform` →
  `netbox_device_platform` → `instance_platform_preset`. (Default change noted
  under Breaking changes.)
- Wired up `mongodb_container_engine` and `patroni_container_engine` as
  the authoritative per-role selectors. Both vars existed in defaults
  but were never consumed — every task, var, and handler in those roles
  read `container_engine | default('docker')` directly. Now the global
  `container_engine` flows through one bridge line in each role's
  `defaults/main.yml`, and all dispatch / conditions / template paths
  read the per-role var. Set `mongodb_container_engine` /
  `patroni_container_engine` explicitly to run one stack on a different
  engine than other roles on the same host group.
- Bridged `hostvars['localhost'].artifacts_dir` in mongodb via a single
  `mongodb_artifacts_dir` var in defaults. Eliminates six scattered
  `hostvars['localhost'].artifacts_dir` references in `passwords.yml` and
  `manage_users.yml`; `mongodb_local_certs_dir` now derives from the
  bridge. Override `mongodb_artifacts_dir` once to relocate every
  mongodb artifact (creds, certs). Patroni's `patroni_credentials_dir`
  was already bridged the same way.
- Moved `mongodb_services` (cluster-type catalog) and `patroni_services`
  (with/without exporter) from `set_fact` in
  `netbox_register_services.yml` to declarative entries in each role's
  `vars/main.yml`. Catalogs are now available from role-load time, not
  just after the netbox-registration task runs. The firewall role's
  `mongodb_services | default([])` lookup now returns the real catalog
  even when NetBox registration is gated off — previously fell back to
  `[]` and silently omitted service ports.
- Extracted per-VM service-registration mechanics into
  `roles/netbox_register/tasks/register_services.yml`. Mongodb /
  patroni's `netbox_register_services.yml` shrunk to a per-host loop
  that includes the role with the relevant catalog. IP auto-resolution
  from `hostvars['localhost'].instances` now lives in `netbox_register`
  (the role that owns localhost dispatch); callers can override by
  passing `netbox_vm_service_ips` explicitly.
- Renamed `_os_preset` → `_platform_preset` across
  `roles/common/vars/main.yml`, `roles/common/tasks/resolve_platform_preset.yml`,
  `roles/instance/defaults/main.yml`, and the four
  `roles/instance_ignition/templates/*.bu.j2` Butane templates. Internal
  rename only — `_platform_preset` is a private `_`-prefixed var
  resolved at role-load time; no inventory-visible change.

### Behaviour notes
- `mongodb_container_engine` and `patroni_container_engine` default to
  `podman` when neither they nor `container_engine` are set. Inventories
  that explicitly set `container_engine: docker` are unaffected.

### Docs
- Documented the **MongoDB 8 ↔ Linux kernel ≥ 6.19 incompatibility** (vendored
  TCMalloc/rseq bug, unfixed upstream as of 2026-06). Added a gotcha with the
  FCOS-build/kernel table to the `mongodb` role README and a condensed entry to
  the top-level README troubleshooting section, cross-referencing the
  `preflight` kernel assertion and the `mongodb_skip_kernel_check` bypass.
  Tracked in [#2](https://github.com/startechnica/ansible-collection-infra/issues/2).

## 1.0.1 (2026-05-25)

### Breaking changes
- Removed `ignition_flavour` knob (`fcos` / `flatcar` / `rhcos` /
  `opensuse`). OS selection is now via `instance_platform_preset`
  using the NetBox platform slug (`fedora-coreos` / `flatcar` /
  `rhel-coreos` / `opensuse-microos`). The per-flavour
  `ignition_flavour_map` is gone; the data lives in
  `_platform_map[<slug>].ignition`. `roles/instance_ignition/vars/`
  was deleted. `ignition_flavour_default.<field>` (used by Butane
  templates and `render_ignition.yml`) is unchanged — still works,
  now sourced from `_platform_map[<preset>].ignition`. Inventory
  migration: replace `ignition_flavour: fcos` with
  `instance_platform_preset: fedora-coreos`, and so on.
- Default values changed from non-empty to empty strings for the
  five vars that now feed the slug-fallback chain. Required for the
  empty-check semantics — a non-empty default would short-circuit
  the slug lookup. Anything reading these vars directly (without
  going through `_resolved_*`) will see `""` until inventory sets
  them or `instance_platform_preset` is configured.
  - `instance_user_name`: `ubuntu` → `""`
  - `instance_init_type`: `ignition` → `""`
  - `container_engine`: `podman` → `""`
  - `content_library_name`, `content_library_template`: already `""`
- Removed `Port` directive from `instance_ssh_configs` / per-VM
  `instances[].ssh_config`. Introduced top-level `instance_ssh_port`
  (and per-VM `instances[].ssh_port`) as the canonical SSH port knob.
  The dict-of-directives approach silently failed on port changes
  because (a) sshd's `Port` is additive across drop-ins so the main
  config's `Port 22` kept 22 open, (b) systemd socket activation
  (`ssh.socket` on Ubuntu 22.04+ / Debian 12) bypasses sshd_config
  entirely, and (c) SELinux on RHEL/Suse blocks non-22 binds without
  `semanage port -a`. The new `instance_ssh_port` task handles all
  three. `ansible_port` derivation in `build_host_groups.yml` and the
  vendored `playbooks/{mongodb,patroni}/_setup.yml` now read
  `item.ssh_port | default(instance_ssh_port)` instead of digging
  into `ssh_configs.Port`. Inventory migration: move
  `instance_ssh_configs.Port: 2222` → `instance_ssh_port: 2222`; per-VM
  `instances[].ssh_config.Port: N` → `instances[].ssh_port: N`. Other
  sshd_config directives stay in `instance_ssh_configs` unchanged.
  Port changes now run a two-phase migration: bind BOTH 22 and the new
  port → verify new port reachable from controller → drop 22. On
  verify failure, sshd stays on both ports so the operator can SSH in
  on 22 to diagnose.
- Renamed `netbox_query_*` → `netbox_lookup_*` (7 vars: `cluster`,
  `site`, `tenant`, `role`, `status`, `tags`, `custom_fields`). The role
  is named `netbox_lookup`, so the user-facing knob prefix should match
  (consistent with `netbox_lookup_enabled`, `netbox_lookup_interface`,
  `netbox_lookup_set_instances`). Affects `roles/netbox_lookup/`:
  `defaults/main.yml`, `meta/argument_specs.yml`, `tasks/query_vms.yml`,
  `vars/main.yml`, `README.md`. Inventory migration: rename the keys in
  inventory or group_vars (old keys are silently ignored).
- Renamed `content_library_template` → `content_library_item_name`.
  vSphere content libraries hold "items" (which may be OVAs, VM
  templates, ISOs, etc.) — the field is the *item name*, not
  specifically a "template". The new name matches what
  `vmware.vmware.content_library_item_info` returns as `library_item_name`
  and removes ambiguity with `content_library_template_prefix` (an
  unrelated auto-import naming knob that stays unchanged). Affects:
  roles/instance/defaults/main.yml, roles/instance/meta/argument_specs.yml,
  roles/instance/tasks/main.yml + validate/template.yml,
  roles/instance_ignition/tasks/{content_library_import,fcos_prepare}.yml,
  roles/common/vars/main.yml (`_platform_map` field rename across all 44
  OS rows + `_resolved_content_library_item_name`),
  roles/common/tasks/resolve_platform_preset.yml (host-fact publish),
  examples/inventory.{full,minimal}.yml + molecule fixture + READMEs +
  mongodb/preflight comment. Inventory migration: rename the key
  `content_library_template:` → `content_library_item_name:` in your
  inventory or group_vars. The ansible-galaxy module name
  `vmware.vmware.deploy_content_library_template` is unchanged — that's
  the upstream module's identifier, not our variable.
- Removed `validation_only` knob and all `- not (validation_only)`
  gates. The flag was an under-used pre-flight short-circuit (skip VM
  creation, only run validators) — equivalent dry-running is better
  served by `ansible-playbook --check` or `--syntax-check`, and the
  validator tasks (validate/vcenter, validate/input, validate/vms,
  validate/template, validate/role, etc.) still run unconditionally
  on every play so any inventory error is caught regardless. Removed
  from: roles/instance/defaults/main.yml (declaration + docstring),
  roles/instance/meta/argument_specs.yml, roles/instance/tasks/main.yml
  (9 `when:` blocks), playbooks/deploy.yml (1 block), plus stale doc
  references in roles/instance/README.md, molecule/default/README.md,
  examples/cloud-init.yml. Inventory migration: drop any
  `validation_only:` lines or `-e validation_only=true` invocations
  — they're silently ignored now.
- Renamed `docker_install` → `docker_enabled`. Aligns with
  `podman_enabled` and `mongodb_enabled` / `patroni_enabled` naming —
  the var is a host-level enable toggle, not just an install action.
  Affects all consumers: instance defaults, validate_inventory,
  build_host_groups (add_host propagation), instance/mongodb/patroni
  main.yml dispatch gates, deploy.yml stage gates, deploy_mongodb.yml /
  deploy_patroni.yml geerlingguy.docker `when:`, fcos.bu.j2 conditional
  blocks. Inventory migration: rename the key in inventory + any
  group_vars files. The task filename `preflight/docker_install_fcos.yml`
  is unchanged (separate concept — "the task that installs Docker on
  FCOS", not the variable).

### Added
- `instance_ignition` Butane templates (`fcos.bu.j2`, `flatcar.bu.j2`,
  `opensuse.bu.j2`, `rhcos.bu.j2`) now render a `Port` directive into
  `/etc/ssh/sshd_config.d/99-ansible.conf` at first boot when
  `instance_ssh_port` (or per-VM `instances[].ssh_port`) is non-22. sshd
  binds the new port directly on first boot — no transitional 22→newport
  migration, no second restart. The post-boot `ssh_config.yml` task still
  runs but its Phase 0 probe detects the already-migrated state and skips
  the transition (only re-writes the drop-in if `instance_ssh_configs`
  adds more directives). Filename intentionally matches what the post-boot
  task writes so both code paths converge on a single sshd_config drop-in.
  SELinux port label (FCOS/RHCOS) and `ssh.socket` disable are still
  handled by the post-boot task's preflight — Butane can't run semanage
  in initramfs.
- `roles/instance_ignition/templates/fcos.bu.j2` —
  `rpm-ostree-install-open-vm-tools.service` now copies the
  `/usr/etc/vmware-tools/` factory payload into `/etc/vmware-tools/`
  at first boot, then runs `systemctl reset-failed + restart` on
  `vgauthd.service`. `rpm-ostree --apply-live` skips the rpm's `%post`
  scriptlet that normally seeds `/etc`, leaving `vgauth.conf` and the
  SAML XSD schemas absent — vgauthd then fails to start (`status=255`)
  on every boot. Each step is `|| true`-guarded so a failure (no
  `/usr/etc/vmware-tools`, vgauthd not present) doesn't poison the
  bootstrap chain. Runs once per VM at first boot, before vmtoolsd is
  started, so vgauthd never enters its failed state.
- `roles/netbox_lookup/vars/main.yml` and
  `roles/netbox_register/vars/main.yml` — wiring-table documentation
  (`_lookup_input_map`, `_register_input_map`, `_*_per_vm_input_map`,
  `_lookup_netbox_query_map`, `_register_netbox_write_map`,
  `_*_output_map`, `_register_side_effect_map`) mapping every external
  var these roles consume to the NetBox API resources they read/write
  and the facts they export. Loaded as role vars so they're
  runtime-inspectable via `debug: var=_lookup_input_map`, but tasks
  don't reference them — they're the single source of truth for
  "edit-FIRST-when-adding-a-field" discipline. Includes shared
  `_netbox_api_headers` (Authorization + Content-Type + Accept) reused
  by every `ansible.builtin.uri` call in both roles.
- `roles/instance/meta/argument_specs.yml`: `content_library_type`
  now has a `description:` documenting why `iso` is intentionally NOT
  in `choices` (ISOs are boot media; no vSphere "deploy VM from ISO"
  API). Surfaces in `ansible-doc -t role` output and in the rejection
  message when an inventory typo specifies `iso`.
- `ansible.utils >=5.1.0` declared as a runtime dependency
  (`common` and `instance` roles use `ansible.utils.ipv4` /
  `ansible.utils.ipv6` filters).
- `meta/runtime.yml` with `requires_ansible: ">=2.15.0"` and the
  `mongodb` action group.
- `.ansible-lint` `kinds:` entry classifying `playbooks/**/_*.yml`
  as task-file fragments (consumed via `include_tasks`).
- `roles/common/vars/main.yml`: canonical OS identity table
  `_platform_map`, keyed by NetBox platform slug and linking
  `ansible_facts.os_family`, `ansible_facts.distribution`, and the
  vSphere `guestId` for each supported guest. The existing
  `_vsphere_guest_id_platform_map` is now derived from it so there's a
  single source of truth. Lives in `common` (not `instance`) so
  `common.validate_inventory` and `common.build_host_groups` — which
  run before `instance` loads its vars — can see slug-derived values.
  Covers Debian/Ubuntu (incl. netbox-community `debian-gnulinux-N-64-bit`
  and `ubuntu-linux-64-bit` slug aliases), RHEL family (RHEL, Rocky,
  AlmaLinux, Oracle Linux, CentOS, Fedora, Fedora CoreOS, RHCOS), Suse
  (SLES, openSUSE Tumbleweed + MicroOS), Flatcar Container Linux,
  Windows desktop / Server 2016–2022, FreeBSD (pfSense), and MikroTik
  RouterOS — 44 rows total. Aliases ordered before canonical slugs so
  the inverse map resolves to the canonical entry per `guestId`.
- Each `_platform_map` row carries extra fields beyond the original
  three: `username` (default OS user), `init_type` (cloud-init /
  ignition / none), `container_engine` (docker for Debian/Suse, podman
  for RedHat, `""` for Windows / FreeBSD / RouterOS),
  `content_library_name` (`<distribution>-images` by convention),
  `content_library_item_name` (defaults to the slug). The 4 ignition
  rows (`fedora-coreos`, `flatcar`, `rhel-coreos`, `opensuse-microos`)
  additionally carry an `ignition:` sub-dict with butane_variant /
  butane_spec_version / butane_template / template_prefix /
  default_channel / channels / metadata_url / ova_url — the data that
  used to live in `ignition_flavour_map`.
- `instance_platform_preset` — new top-level knob that picks an OS row
  from `_platform_map` to supply slug-based defaults for
  `content_library_name`, `content_library_item_name`, `instance_user_name`,
  `instance_init_type`, and `container_engine`. Explicit inventory
  overrides on those five vars still win; unset (empty) falls back to
  the slug-derived value, then to a hardcoded final fallback
  (`ubuntu` / `ignition` / `podman` / `""`).
- `_resolved_content_library_name`, `_resolved_content_library_item_name`,
  `_resolved_instance_user_name`, `_resolved_instance_init_type`,
  `_resolved_container_engine` — derived vars in
  `roles/common/vars/main.yml` that implement the slug-fallback
  precedence chain (inventory non-empty > `_platform_map[<preset>].<field>`
  > hardcoded final fallback). Consumers across `common`, `instance`,
  `instance_cloud_init`, `instance_ignition`, `mongodb`, `patroni`,
  and `preflight` now read these `_resolved_*` names so the slug-based
  preset reaches every dispatch site (ansible_user on dynamic hosts,
  container runtime selection, Butane templates, etc.).

### Fixed
- `roles/instance/tasks/ssh_config.yml` Phase 0 probe regex used POSIX
  `[[:space:]]` which Python's `re.match` doesn't support — silently
  mismatched, causing `_already_migrated` to be `False` even when the
  drop-in was correctly written. Rewritten using `regex_search` with
  `\s` (Python-compatible) to parse port numbers out of each `Port` line.
- `playbooks/patroni/remove-node.yml`: uninstall dispatch was importing
  the patroni role with `tasks_from: uninstall.yml`, a file that doesn't
  exist — would have failed at runtime. Now dispatches via
  `patroni_action: uninstall` through the role's `main.yml`, matching
  the working `playbooks/mongodb/remove-node.yml` pattern.
- Several `hosts:` templates in add-node / remove-node playbooks
  (`groups['mongodb_nodes'][0]`, `{{ target_node }}`) crashed
  ansible-playbook `--syntax-check` when the variable wasn't yet
  defined. Refactored with `| default('localhost')` fallbacks; same
  runtime behavior.
- `roles/common/tasks/validate_inventory.yml`: `instance_user_name`
  precondition now optional when init_type is ignition (the slug map
  or `ignition_flavour_default.instance_user_name` supplies it). Auth
  check now accepts `instance_user_ssh_authorized_keys` in addition to
  `instance_user_password` / `instance_user_ssh_private_key`, matching
  what Butane / cloud-init templates actually consume.
- `roles/instance/tasks/ssh_config.yml` Phase 0 probe rewritten from
  `vmware_vm_shell` + sudo + redirected grep + file fetch to a single
  `vmware_guest_file_operation: fetch` of the 0644 drop-in (no sudo)
  + local `slurp` + regex_search interpret. The old shell-based probe
  failed with opaque `"Failed to execute command"` on some
  `community.vmware` versions and required `wait_for_process: true`.
  Also removed `no_log: true` from all probe steps so guest API errors
  surface cleanly. `failed_when: false` on the fetch treats "file
  missing" as "not migrated yet" (safe default — full migration runs).
- `roles/instance/tasks/ssh_config.yml` Preflight task hardening:
  dropped `set -e` (its interaction with `||` chains and command
  substitution caused exit code 2), simplified semanage availability
  check to `command -v semanage` (drops OS_FAMILY case statement),
  changed `2>/dev/null` → `2>&1` so stderr is captured, removed
  `no_log: true`, added explicit `exit 0`. Then fixed YAML folded-
  scalar indentation that was preserving newlines and causing `||` at
  start of line (bash exit 2). Documented the YAML-indentation gotcha
  in a warning comment.
- `roles/instance/tasks/ssh_config.yml` Phase 1 + Phase 3 install
  tasks: removed `no_log: true` and added `2>&1` to each shell step so
  stderr surfaces in the failure result (was hiding sudo-TTY /
  sshd -t / ssh.socket conflicts). Replaced `(... 2>/dev/null || ...)`
  subshell with `{ ... 2>&1 || ... 2>&1; }` group so exit code
  propagates correctly.
- `roles/instance_ignition/tasks/post_boot_extras.yml`: previous
  implementation used `ansible.builtin.stat` / `command` modules
  which run on the Ansible CONTROLLER, not the VM — meaning the
  `/usr/etc/vmware-tools` → `/etc/vmware-tools` recovery never
  executed against any actual VM (the stat always returned False on
  the controller). Rewritten to use `vmware_vm_shell` so the cp +
  vgauthd reset actually runs on the target. Dropped
  `wait_for_process: true` because restarting vgauthd briefly takes
  the guest-auth daemon offline and trips controller-side
  `ListProcessesInGuest` polls with `"None (unexpected)"`; the action
  is idempotent + fast and nothing downstream depends on synchronous
  completion. Gated to skip when the schemas file is already present
  (true for any VM provisioned with the Butane fix from this release).

### Changed
- `galaxy.yml`: dependency version floors aligned with the higher
  bounds already in `requirements.yml` (community.crypto >=3.2.0,
  community.docker >=5.2.0, community.general >=12.6.0,
  community.mongodb >=1.7.12, community.postgresql >=3.14.3,
  community.vmware >=6.2.0, vmware.vmware >=2.8.0). `netbox.netbox`
  lowered from `>=4.1.0` to `>=3.22.0` — no 4.x exists on Galaxy.
- `.yamllint`: `line-length` max raised 180→200 to accommodate inline
  Jinja `{%- if -%}` blocks in debug messages.
- CI: lint + syntax-check now run on every branch push (not just
  main). Molecule remains gated to main / PRs / manual dispatch.
- CI actions bumped: `actions/checkout@v4`→`v6`,
  `actions/setup-python@v5`→`v6` (Node 24 support).
- `roles/geerlingguy.docker/` untracked from git — pinned in
  `requirements.yml` and reinstalled fresh by CI, so the vendored copy
  only drifted. Added to `.gitignore` and `galaxy.yml` `build_ignore`.
- `roles/instance/tasks/main.yml` flow restructure: power-on folded
  into the per-VM `prepare_all.yml` loop (Phase 2a), so VM N starts
  booting while VM N+1 is still being prepared — overlapping boot
  wall-clock with the prepare serialization that was previously paid
  up-front. The separate `poweron_all.yml` Phase 2b is deleted; its
  bookkeeping (per-init-type `poweron_mark.yml`) now operates on a
  single `instance` loop_var instead of looping over the full list.
  Serial-ack semantics preserved (deterministic order, no vSphere
  thundering-herd). `wait_for_boot_all.yml` (parallel batch wait)
  unchanged — still folds N×~17 min serial waits into one.
- `roles/netbox_lookup/` + `roles/netbox_register/` task files: 14
  inline `headers:` blocks across 5 task files replaced with
  `headers: "{{ _netbox_api_headers }}"` so adding/removing an HTTP
  header is a single-file edit. Added `Accept: application/json` while
  consolidating — harmless on GET, correct for POST/PATCH.
- `roles/netbox_lookup/` + `roles/netbox_register/` task files:
  removed 20 redundant `| default(false)` filters on
  `netbox_connect.validate_certs` across 9 task files. The value is
  already defaulted to `false` in each role's `defaults/main.yml` —
  the shadow default was misleading (suggesting the key might be unset,
  which it can't be at role entry).
- `roles/instance/tasks/validate/{template,input}.yml`: two
  `success_msg:` strings converted from folded `>-` scalar to YAML
  array form so multi-line output in `-v` runs renders cleanly per
  line instead of as one wrapped string.

### Lint cleanup
- ~60 missing task names added across mongodb / patroni entry-point
  playbooks and the `instance` role's molecule converge tests.
- Handler / task name casing normalized.
- 7× intentional shell probes (curl mTLS, `systemctl is-active`,
  `rpm -q`) marked `# noqa: command-instead-of-module` with inline
  rationale.
- Long YAML loop tables (TLS cert specs, service maps) had
  column-alignment padding stripped to satisfy yamllint defaults.
- FQCN module names (`ansible.builtin.uri`, `ansible.builtin.file`,
  `ansible.builtin.async_status`, `ansible.builtin.command`,
  `ansible.builtin.fail`, `ansible.builtin.import_tasks`,
  `ansible.builtin.template`) added across netbox_lookup,
  netbox_register, instance_ignition, patroni backup, and
  `tests/render-templates.yml`. Resolves `fqcn[action-core]`.
- Explicit `mode:` on `file` / `template` / `directory` tasks in
  patroni backup, instance_ignition render, and template-render
  tests. Resolves `risky-file-permissions`.

## 1.0.0 (2026-05-25)
- Initial release