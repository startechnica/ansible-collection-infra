# Changelog

All notable changes to this collection are documented in this file. The format
is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this collection adheres to [Semantic Versioning](https://semver.org/).

## 1.0.3 (unreleased)

### Breaking changes
- **Removed `s3_retain_days`.** Remote (S3) backup retention now always follows
  `mongodb_backup_retain_days` — the var was a needless second knob (its default
  was already `{{ mongodb_backup_retain_days }}`). Only affects deployments that
  set `s3_retain_days` to a value *different* from `mongodb_backup_retain_days`;
  set `mongodb_backup_retain_days` to the desired window instead. Migration:
  [docs/UPGRADING.md](docs/UPGRADING.md).
- **Renamed `s3_prefix` → `mongodb_backup_s3_prefix`** and changed its default
  from `mongodb/{{ mongodb_network }}` to a cluster-namespaced, slugified path
  `mongodb-{{ mongodb_cluster_name | slugify }}` (e.g. `mongodb-mongodb-cluster`).
  This **moves the default S3 backup path** (mongodump + PBM under
  `<prefix>/pbm`). Existing objects under the old prefix are not migrated. Any
  inventory that set `s3_prefix` must rename it to `mongodb_backup_s3_prefix`; to
  keep the previous location, set `mongodb_backup_s3_prefix: "mongodb/{{ mongodb_network }}"`.
  `mongodb_network` still names the docker-compose network (docker engine only).

### Added
- **Unified MongoDB backup engine selector (`mongodb_backup_type: mongodump|pbm`).**
  Configures the active backup engine for both scheduled and on-demand backups.
  When set to `pbm`, `mongodb_action: backup` and scheduled timers dispatch to
  Percona Backup for MongoDB (PBM); when `mongodump`, traditional containerized
  mongodump is used.
- **Selectable PBM object-storage client.** New
  `mongodb_backup_storage_type: minio|s3` setting defaults to PBM's native MinIO
  client for MinIO and other S3-compatible endpoints, avoiding AWS SDK request
  signing incompatibilities with proxies that rewrite signed headers. Set it to
  `s3` for Amazon S3 or endpoints that require PBM's AWS SDK backend.
- **Patroni standby-cluster support (DR / off-site replica).** New opt-in
  (`patroni_standby_enabled: true`) that deploys a full Patroni cluster whose
  leader is a **Standby Leader** continuously replaying a *remote* primary —
  **streaming** with a **WAL-G `wal-fetch` fallback** from the primary's S3
  archive. New vars: `patroni_standby_primary_host` / `_port` / `_slot` /
  `_sslmode` / `_create_replica_methods` / `_walg_prefix`, plus cross-cluster
  wiring `patroni_shared_ca_dir` (reuse one CA across clusters so `verify-ca`
  streaming works) and `patroni_replication_cidrs` (primary-side pg_hba for
  remote standby IPs). Adds `bootstrap.dcs.standby_cluster` to the config
  template (skipping `post_bootstrap` role creation on a standby), a
  post-bootstrap `reconcile_standby.yml` (keeps the DCS block in sync, both
  directions), standby-aware leader detection (accepts `Standby Leader`), early
  guardrails (requires a primary host + a scope distinct from the primary +
  shared-CA-or-`require`), and a manual promotion playbook
  `playbooks/patroni/standby-promote.yml` (removes `standby_cluster` from DCS →
  promotes to an independent primary). Design + full runbook:
  [docs/design/patroni-standby-cluster.md](docs/design/patroni-standby-cluster.md).
- **Per-cluster S3 prefixes for Patroni backups** — WAL-G and etcd snapshots had
  no scope namespacing, so multiple Patroni clusters sharing one bucket collided
  (`basebackups_005/`, `wal_005/`, `etcd-snapshots/etcd-snapshot-<STAMP>.db`).
  Two new vars isolate them:
  Both are scope-namespaced by default (bare key prefixes joined under
  `s3_bucket`), so clusters don't collide out of the box:
  - **`patroni_walg_s3_prefix`** (default `patroni-walg-{{ patroni_scope }}`) —
    WAL-G base backups + WAL archive. A standby's
    `patroni_standby_primary_walg_prefix` points at the primary's prefix (same
    bucket) for its wal-fetch fallback. Set `""` to use the bucket root.
  - **`patroni_etcd_s3_prefix`** (default `patroni-etcd-{{ patroni_scope }}`) —
    etcd snapshots.
  NOTE: these **change the default S3 upload paths** — WAL-G moves from the
  bucket root to `patroni-walg-<scope>/`, and etcd snapshots from
  `etcd-snapshots/` to `patroni-etcd-<scope>/`. Existing objects under the old
  paths are not migrated; for an already-running cluster, either pin the old
  values (`patroni_walg_s3_prefix: ""`, `patroni_etcd_s3_prefix: etcd-snapshots`)
  or take a fresh base backup at the new prefix before relying on PITR.
- **Percona Backup for MongoDB (PBM) — sharded-cluster PITR.** New opt-in
  (`mongodb_pbm_enabled: true`) that deploys one `pbm-agent` next to every
  data-bearing `mongod` (two per host on sharded clusters: shard + configsvr;
  one per host on replica sets), reading each replica set's oplog directly —
  the cluster-consistent backup + PITR that `mongodump --oplog` via `mongos`
  can't provide. Agents authenticate with a new X.509 client cert
  (`CN=mongodb-pbm`) reusing the existing PKI, and store backups in the shared
  `s3_*` target via PBM's native S3. Engine-dispatched (Quadlet on podman,
  standalone containers on docker). Day-2: `mongodb_action: pbm-setup|pbm-backup|pbm-restore|pbm-status`,
  `playbooks/mongodb_pbm_setup.yml` (FQCN-addressable) +
  `playbooks/mongodb/pbm-{backup,restore,status}.yml`. Scheduled base backups +
  retention reuse the **shared** backup knobs — the timer fires on
  `mongodb_backup_schedule` and a post-backup `pbm cleanup` prunes base backups
  + oplog chunks older than `mongodb_backup_retain_days` (one schedule + one
  retention window for both the mongodump path and PBM). Backup compression is
  tunable via `mongodb_pbm_compression` (default `zstd`) + optional
  `mongodb_pbm_compression_level` (applied to base backups and PITR slices).
  `pbm-setup` retrofits PBM onto
  an already-running cluster without reprovisioning — it creates the per-RS PBM
  user via member-cert (`__system`) auth and deploys agents with no container
  recreation. Logical backups + logical PITR on the Community image (physical
  needs PSMDB). Design: docs/design/mongodb-pbm.md.
- **PBM storage init + base backup on provision** — after deploying agents and
  applying config, the role now force-resyncs PBM storage (clearing *"storage is
  not initialized"*) and lays down a base backup when none exists, so PITR has
  the anchor it requires to start slicing (*"no backup found. full backup is
  required to start PITR"*). Idempotent via `mongodb_pbm_init_backup` (default
  `true`) — re-runs never create extra backups. Also fixes PBM S3 against
  path-style-only / strict-checksum gateways: `forcePathStyle` (new
  `s3_force_path_style`, default `true`) avoids `HeadObject 403`, and
  `AWS_REQUEST/RESPONSE_CHECKSUM_*=when_required` on the agents avoids
  `XAmzContentSHA256Mismatch` from the AWS SDK v2 default checksums.
- **MongoDB scheduled backups** — provisioning now installs a host-level
  systemd timer (`mongodb-backup.timer` → `mongodb-backup.service`) on one
  node that runs the same mongodump → prune → S3-upload flow as
  `playbooks/mongodb/backup.yml`, on `mongodb_backup_schedule` (default
  `*-*-* 02:00:00`). Mirrors patroni's `walg-cron.timer`; `Persistent=true`
  catches up missed runs, output goes to `/var/log/mongodb-backup.log`. Master
  switch `mongodb_backup_enabled` (default `true`) or an empty
  `mongodb_backup_schedule` tears down the timer. `mongodb_backup_mode`
  (`local`/`s3`, defaults to `s3` when `s3_bucket` is set) selects the
  destination: `local` keeps the dump on the node under retention, `s3`
  uploads then deletes the local copy (pure-S3). Before deleting, the uploaded
  object is re-read and SHA-256 compared end-to-end against the local archive;
  a mismatch fails the run and keeps the local copy. Applies to both the
  on-demand and scheduled flows.
- **PITR from S3** — `playbooks/mongodb/pitr.yml` gained `backup_source: s3`
  (mirroring `verify-backup.yml`): it downloads + extracts the base mongodump
  from the bucket before replaying, so point-in-time recovery works when the
  local copy is gone (e.g. `mongodb_backup_mode: s3`). Defaults to latest
  archive; `-e backup_name=mongodump_…` selects a specific one. `backup_path`
  is no longer required when `backup_source=s3`.
- **MongoDB backup PITR + TLS support** — `mongodb_backup` gained an `oplog`
  param (wired via `mongodb_backup_pitr`, default `false`) that passes
  `--oplog` so dumps are usable by `playbooks/mongodb/pitr.yml`. Only valid
  for replica-set deployments — `mongodump --oplog` is rejected against a
  mongos, so keep it off on sharded clusters. Both
  `mongodb_backup` and `mongodb_restore` gained `tls`/`tls_host_ca_file`/
  `tls_cert_file` params; the backup/restore tasks now auto-pass TLS when
  `mongos_tls_mode` is `requireTLS`/`preferTLS`, so backups keep working if
  mongos is hardened to `requireTLS`.

- **Content-library OVA import is now retried** — the auto-import path wraps the
  `import_content_library_ovf` call in a bounded retry loop
  (`content_library_import_retries`, default `3`; `content_library_import_retry_delay`,
  default `30`s; `content_library_import_timeout`, default `3600`s). vCenter
  streams the OVA host→datastore over NFC, and that transfer fails transiently
  mid-flight (*"IO error during transfer of …vmdk: Pipe closed"*) on network /
  NFC / vSAN blips; a single blip no longer aborts the whole provision. Each
  attempt deletes any partial item first — the module won't overwrite an
  existing item, so without the pre-clean a retry would silently "succeed"
  against a corrupt OVA. If all attempts fail the run stops loudly with a
  transfer-vs-config diagnostic.
- **DVS NIC binding is verified after provision** — a post-configure assert
  re-probes each NIC placed on a *distributed* switch and fails loudly if it
  didn't bind (`portgroup_key` null). Previously `vmware_guest` reported
  `changed=true` even when a DVS NIC landed in `unrecoverableError` /
  `portgroup_key=None`, so the VM powered on with a dead adapter showing
  "(disconnected)" and no guest IP — with no error at provision time, surfacing
  days later. Standard-vSwitch NICs (which legitimately have a null
  `portgroup_key`) are exempt.

### Changed
- **Percona sidecar image defaults bumped.** `mongodb_exporter_image`
  `0.51.0` → `0.52.0`, `mongodb_pbm_image` `2.14.0` → `2.15.0`. Standalone
  Go binaries — unaffected by the kernel issue below.
- **`mongodb_version` default → `8.0.28`.** Pinned to the **LTS** line that
  satisfies PBM 2.15's LTS-only support (`7.0.x`/`8.0.x` only — rapid releases
  `8.1`/`8.2`/`8.3` are rejected at the agent) and sharded-cluster PITR
  (mongodump --oplog can't run via mongos; PBM is required). This does **not**
  make 8.0.28 compatible with Linux kernels >= 6.19; those hosts must use a
  supported kernel/OS before deploying MongoDB.

### Fixed
- **Kernel-compatibility gate blocks every MongoDB version on Linux kernel
  `>= 6.19`.** The vendored-TCMalloc/rseq incompatibility is open-ended (no
  `7.0.14+` escape) and affects the 8.0 LTS line, including `8.0.28`. Preflight
  now fails before starting containers, rather than allowing mongod to
  crash-loop and producing a misleading port-listener timeout. The former
  version-gated `mongodb_kernel_guard_min_version` and `mongodb_fixed_kernel`
  variables are retained but **inert** for inventory compatibility. On an
  affected host, use a kernel `< 6.19` (via `content_library_item_name`) or a
  non-affected OS; choosing an older MongoDB version is not a resolution.
  Tracking: [SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912).
- **PBM 2.15 doesn't support MongoDB rapid releases — now caught at preflight.**
  PBM 2.15 only certifies against MongoDB LTS releases (`7.0.x`, `8.0.x`);
  mid-train rapid releases (`8.1`, `8.2`, `8.3`, …) are rejected at the
  agent: backup hangs at "starting" and fails 30s later with "PBM does not
  support minor versions of MongoDB" — silent until the first timer tick at
  02:00. New preflight check fails fast with a clear message when
  `mongodb_pbm_enabled: true` is combined with a non-LTS MongoDB version, so
  this is caught at provision time instead.
  ([Percona compatibility matrix](https://docs.percona.com/percona-backup-mongodb/details/versions.html))
- **`mongodump --oplog` on a sharded cluster is a dead flag — now caught at
  preflight.** On `mongodb_cluster_type: sharded`, setting
  `mongodb_backup_pitr: true` WITHOUT also setting `mongodb_pbm_enabled: true`
  causes the scheduled backup to fail at the first 02:00 timer tick with
  "can't use --oplog option when dumping from a mongos" — mongodump cannot
  produce cluster-consistent PITR on sharded, only PBM can. New preflight
  check fails fast with a clear message for this invalid combination.
- **PBM initial base-backup crashed on a freshly-resynced cluster** — `pbm list
  --out json` returns `{"snapshots": null}` (an explicit null, not a missing
  key) right after a storage force-resync with no backups yet, so the
  `.snapshots | default([]) | length` guard hit `NoneType has no len()` and
  failed the provision. Now uses `default([], true)` (replaces null, not just
  undefined) and tolerates empty stdout. Only surfaced on the first PBM-enabled
  run before any backup existed.
- **Transient `IncompleteRead` on the post-rebind NIC re-probe** — the
  `vmware_guest_info` verification read after a DVS NIC rebind pulls the whole VM
  object, the largest response in the provision flow, and could be truncated
  mid-body on a busy vCenter (`IncompleteRead(N bytes read)`), aborting the run
  even though the rebind itself succeeded. The idempotent read now retries
  (`until` / 5×) instead of failing.
- **Distributed-portgroup NICs deployed disconnected** — content-library OVFs
  create the NIC with a standard-vSwitch backing (`NetworkBackingInfo`), and
  `vmware_guest`'s `networks:` can't convert that to a distributed-vSwitch
  backing — even with `dvswitch_name` it only renames the portgroup on the wrong
  backing type, leaving the NIC in `unrecoverableError` / `portgroup_key=None`
  ("(disconnected)", no guest IP). `configure_hardware` now rebinds every DVS NIC
  with the purpose-built `community.vmware.vmware_guest_network` (matched by
  device label), which reliably replaces the backing. Per-NIC `dvswitch` or the
  role-level `portgroup_dvswitch_name` selects the switch; standard-vSwitch NICs
  are unaffected.
- **FCOS auto-import never ran (dead since it was added)** — the gate required
  `content_library_item_name` to be empty, but `resolve_platform_preset`
  backfills it from the platform preset (`fedora-coreos`) before the gate is
  reached, so its length was never 0 and the import was silently skipped for
  every un-pinned inventory. The resolver now captures the *explicit-pin* intent
  in `_content_library_item_pinned` before the backfill, and the gate keys off
  that. Pinned inventories still skip auto-import and use their exact OVA.
- **FCOS metadata fetch built `streams/.json` (404) when the preset was unset** —
  `ignition_channel` is resolved inside the `common` role, which didn't inherit
  the `instance` role's `fedora-coreos` default, so an inventory that set
  `instance_init_type: ignition` without `instance_platform_preset` resolved the
  channel to empty and fetched `https://…/streams/.json`. `common` now carries
  its own `instance_platform_preset` default, and `fcos_prepare` asserts a
  channel is resolvable before building the URL (clear error instead of a 404).
- **MongoDB kernel gate rejected fixed kernels ≥ 7.0.14** — the preflight check
  refused any kernel `≥ 6.19`, an open-ended floor. The TCMalloc/rseq
  incompatibility is a *bounded* range (6.19 through 7.0.13); Linux 7.0.14+
  resolves it kernel-side. The gate is now a range (`< 6.19` **or** `≥ 7.0.14`
  via the new `mongodb_fixed_kernel`, default `7.0.14`), so hosts on a fixed
  kernel 7 pass. See [SERVER-121912](https://jira.mongodb.org/browse/SERVER-121912).
- **FCOS layered python3 on provision-only VMs** — the Ignition config layered
  `python3` at first boot unconditionally, even on hosts that run no service
  stage. python3 is the Ansible runtime for the mongodb/patroni/docker roles
  (Patroni runs on podman, so it's not docker-specific), so the layer is now
  gated on `docker_enabled or podman_enabled`. Provision-only hosts (both flags
  false) stay python-free; if a service stage later runs against one,
  `preflight/venv.yml` layers python on demand as before.
- **etcd snapshot cleanup failed on the distroless etcd image** — the snapshot
  script ran `${ENGINE} exec etcd rm …` to delete its in-container temp file,
  but the etcd 3.6 image ships no shell/coreutils (`exec: "rm": executable file
  not found`). Every run logged the error and leaked a 1+ GB temp file into the
  container's writable layer. The snapshot now writes into the bind-mounted data
  dir and all cleanup happens host-side — no `exec`, no dependency on any
  in-container binary.
- **Backups passed `--tls` for `preferTLS`, breaking on older mongodump** — the
  backup/restore TLS auto-gate fired for both `requireTLS` and `preferTLS`, but
  the dump connects over loopback inside the mongos netns where `preferTLS`
  accepts plaintext. Passing `--tls` there is unnecessary and fails outright on
  mongo images whose `mongodump` predates the `--tls` flag (*"unknown option
  tls"*). TLS is now forced only for `requireTLS`.
- **S3 credential injection broke on keys starting with a digit** — the
  `MC_HOST_s3` URL was built with `regex_replace('^(https?://)', '\1' ~
  s3_access_key ~ …)`; when `s3_access_key` began with a digit, `\1` + digit
  parsed as backreference group 1N (e.g. `\19`), failing with *"invalid group
  reference"*. Switched to the unambiguous `\g<1>` group syntax across all
  backup/verify/PITR S3 templates. (Pre-existing since 1.0.2.)
- **MongoDB day-2 backup playbooks were docker-only** — `pitr.yml` and
  `verify-backup.yml` hardcoded `docker`/`community.docker.*` for their
  disposable scratch containers, so they failed on podman hosts (the default
  `mongodb_container_engine`). Both now drive container lifecycle through
  `{{ mongodb_container_engine }}` (a single `run`/`exec`/`volume`/`rm` path
  that works on docker and podman), and `pitr.yml`'s S3 fetch likewise.
- **PBM S3 used virtual-hosted addressing, 403ing on path-style gateways** — the
  rendered PBM storage config set no addressing style, so the AWS SDK defaulted
  to virtual-hosted (`<bucket>.<endpoint>`), which most S3-compatible gateways
  (Ceph RGW, MinIO, custom) reject with `HeadObject … 403 Forbidden` even though
  wal-g (which forces path-style) works against the same bucket. Added
  `s3_force_path_style` (default `"true"`, mirroring patroni's
  `AWS_S3_FORCE_PATH_STYLE`) and wired it to PBM's `storage.s3.forcePathStyle`.
- **PBM `pbm-status` playbook moved to the playbooks root** —
  `playbooks/mongodb/pbm-status.yml` → `playbooks/mongodb_pbm_status.yml`, now
  FQCN-addressable (`startechnica.infra.mongodb_pbm_status`) like
  `mongodb_pbm_setup`.

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