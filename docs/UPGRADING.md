# Upgrading

The collection follows [Semantic Versioning](https://semver.org/) with one
caveat: **pre-1.0 minor bumps may contain breaking changes**. Read the release
notes in [../CHANGELOG.md](../CHANGELOG.md) before upgrading; this file
documents the migration steps for breaking changes.

## 1.0.3 (unreleased)

### Breaking — every `mongodb` role variable is now `mongodb_`-prefixed

`roles/mongodb/defaults/main.yml` is split into `defaults/main/*.yml` (one file
per topic, like `patroni`), and the 22 variables that weren't namespaced have
been renamed. There are **no aliases** — an inventory using an old name is
silently ignored and the role's default applies.

| Old name | New name |
| --- | --- |
| `debug` | `mongodb_debug` (defaults to `debug`, so `-e debug=true` still works) |
| `mongod_mem_limit_mb` | `mongodb_mongod_mem_limit_mb` |
| `configsvr_mem_limit_mb` | `mongodb_configsvr_mem_limit_mb` |
| `mongos_mem_limit_mb` | `mongodb_mongos_mem_limit_mb` |
| `mongod_oom_score_adj` | `mongodb_mongod_oom_score_adj` |
| `configsvr_oom_score_adj` | `mongodb_configsvr_oom_score_adj` |
| `mongos_oom_score_adj` | `mongodb_mongos_oom_score_adj` |
| `mongod_tls_mode` | `mongodb_mongod_tls_mode` |
| `configsvr_tls_mode` | `mongodb_configsvr_tls_mode` |
| `mongos_tls_mode` | `mongodb_mongos_tls_mode` |
| `ssl_days` | `mongodb_tls_days` |
| `ssl_ca_days` | `mongodb_tls_ca_days` |
| `tls_key_type` | `mongodb_tls_key_type` |
| `tls_key_curve` | `mongodb_tls_key_curve` |
| `tls_key_size` | `mongodb_tls_key_size` |
| `s3_endpoint` | `mongodb_s3_endpoint` |
| `s3_bucket` | `mongodb_s3_bucket` |
| `s3_access_key` | `mongodb_s3_access_key` |
| `s3_secret_key` | `mongodb_s3_secret_key` |
| `s3_region` | `mongodb_s3_region` |
| `s3_force_path_style` | `mongodb_s3_force_path_style` |
| `s3_client_image` | `mongodb_s3_client_image` |

#### The one that bites quietly: S3

**`s3_*` and `tls_key_*` are still valid variables — they belong to `patroni`
now.** So a dual-stack inventory that set them once for both roles keeps
validating, keeps running, and produces no error. What changes is that mongodb
stops seeing them:

- `mongodb_s3_bucket` is empty → `mongodb_backup_mode` falls back to `local`,
  and **scheduled MongoDB backups stop being uploaded to S3.** They still run,
  still succeed, still write to `mongodb_backup_dir` on the node. Nothing fails.
- PBM's storage config loses its endpoint and credentials, so `pbm` backups
  fail at the agent rather than at deploy time.

Check whether you are affected:

```bash
grep -rlE '^\s*s3_(bucket|endpoint|access_key|secret_key):' inventories/ \
  | xargs grep -l 'mongodb_enabled: *true'
```

**Action:** in every inventory that runs mongodb, add a `mongodb_s3_*` set. The
two roles may point at the same bucket — only the variable names are per-role:

```yaml
# patroni: WAL-G + etcd snapshots (unchanged)
s3_endpoint: https://s3.example.com
s3_bucket: myproject-backups
s3_access_key: "{{ vault_s3_access_key }}"
s3_secret_key: "{{ vault_s3_secret_key }}"

# mongodb: mongodump upload + PBM (new)
mongodb_s3_endpoint: https://s3.example.com
mongodb_s3_bucket: myproject-backups
mongodb_s3_access_key: "{{ vault_s3_access_key }}"
mongodb_s3_secret_key: "{{ vault_s3_secret_key }}"
```

For an inventory that runs **only** mongodb, rename in place instead of
duplicating:

```bash
sed -i -E 's/\bs3_(bucket|endpoint|access_key|secret_key|region|force_path_style):/mongodb_s3_\1:/' inventories/<inv>.yml
```

#### Why

`tls_key_curve` was defined by **both** roles with **different** values —
`secp256r1` for mongodb, `secp384r1` for patroni. Both feed certificate
generation, so whichever role's defaults loaded last silently decided the curve
of the other's certificates. That one collision is why cross-role variable
imports (added in 1.0.3 for the memory budget) had to be kept to a single
`defaults_from` file, and it would have recurred with every new shared name.

After the rename the two roles share **no** variable name at all, which
`tests/preflight_mem_budget.yml` asserts directly by diffing the two roles'
defaults directories — alongside a second assertion that nothing in mongodb's
defaults is unprefixed.

That includes `debug`, which mongodb used to declare like nine other roles do.
It is now `mongodb_debug`, defaulting to `{{ debug | default(false) }}` — the
role *reads* the collection-wide value without owning the name, so
`-e debug=true` still reaches it and nothing else in the play gets its `debug`
overwritten by whichever role's defaults happened to load last. Same shape as
`mongodb_container_engine` falling back to `container_engine`. Note that nothing
in the role reads it yet: no mongodb task has debug-gated output today, so this
rename changes no behaviour. If you set `debug` for mongodb specifically, set
`mongodb_debug` instead.

### Breaking — `pgbouncer_default_pool_size` renamed, and PgBouncer pool sizes are now real variables

`templates/pgbouncer.ini.j2` hardcoded `default_pool_size = 50`,
`min_pool_size = 10`, `reserve_pool_size = 10` and `max_client_conn = 1000`.
`pgbouncer_default_pool_size` existed as a variable set to `20`, but **nothing
read it** — the effective pool size has always been 50. All four are now wired
to variables:

| Setting | Variable | Default |
| --- | --- | --- |
| `max_client_conn` | `patroni_pgbouncer_max_client_conn` | `1000` |
| `default_pool_size` | `patroni_pgbouncer_default_pool_size` | `50` |
| `reserve_pool_size` | `patroni_pgbouncer_reserve_pool_size` | `10` |
| `min_pool_size` | `patroni_pgbouncer_min_pool_size` | `10` |

Each default equals the literal the template already rendered, so **the
rendered config is byte-identical unless you set one of the new variables.**

**Why a rename rather than switching the old name on:** wiring
`pgbouncer_default_pool_size` up as-is would have dropped every deployment that
set it from 50 server connections per pool to 20 — a silent capacity cut caused
by "fixing" the variable. Renaming makes the old key inert, which is what it
already was.

**Action:** none, unless you set `pgbouncer_default_pool_size` in inventory. If
you did, it was never taking effect; decide what you actually want and set
`patroni_pgbouncer_default_pool_size`. To keep today's behaviour, remove the old
key and set nothing.

### New — PostgreSQL `max_connections` is a variable, and is reconciled post-bootstrap

`max_connections` was the literal `200` in `templates/patroni.yml.j2`. It is now
`patroni_pg_max_connections`, default `200` — the same value, so nothing changes
on upgrade.

Two things to know before you change it:

- **It is a postmaster parameter.** `bootstrap.dcs` only seeds the cluster at
  first init, so the new `shared/reconcile_pg_parameters.yml` pushes the value
  to DCS via `patronictl edit-config` when it differs — the same bridge
  `reconcile_archiving.yml` provides for `archive_command`. That leaves every
  member in `pending_restart`; the role reports this and **does not restart
  anything**, because a rolling restart is an operator's decision.
- **The inventory value now wins over an out-of-band `patronictl edit-config`.**
  If you have hand-tuned `max_connections` on a live cluster, set
  `patroni_pg_max_connections` to match *before* the next provision run or it
  will be pushed back to 200. Check with:

  ```bash
  patronictl -c /etc/patroni/patroni.yml show-config | grep max_connections
  ```

When lowering the value, restart the leader first: a replica whose
`max_connections` is below the primary's refuses to start (hot standby requires
`>=`).

### Breaking — `s3_retain_days` removed

The `s3_retain_days` variable is gone. S3 backup retention now always follows
`mongodb_backup_retain_days`, removing a redundant second knob (its default was
already `{{ mongodb_backup_retain_days }}`).

**What you'll see:** if you never set `s3_retain_days`, nothing changes —
behaviour is identical. If you set it to a value *different* from
`mongodb_backup_retain_days`, remote objects are now pruned on the
`mongodb_backup_retain_days` window instead.

**Action:** set `mongodb_backup_retain_days` to your desired retention (it
governs both local and S3 pruning) and remove any `s3_retain_days` from
inventory.

```yaml
mongodb_backup_retain_days: 14   # applies to local dumps and S3 objects
```

### Breaking — `vip_manager` renamed to `patroni_vip_engine`

The Patroni VIP selector moved into the role's `patroni_vip_*` namespace.
Accepted values are unchanged: `vip-manager` (default), `keepalived`, `none`.
There is no alias — the role no longer reads `vip_manager`.

**What you'll see:** if you never set `vip_manager`, or set it to `vip-manager`
or `none`, nothing changes (`none` was only accepted with `patroni_vip_address`
empty, which disables the VIP on its own). If you set
`vip_manager: keepalived` and don't rename it, the old key is ignored and the
next run switches the cluster to vip-manager:

- **docker:** keepalived drops out of the rendered compose file and is removed
  as an orphan; vip-manager takes over the VIP.
- **podman:** the existing `keepalived.container` Quadlet is not removed, so
  keepalived keeps running alongside the new vip-manager, both managing the
  same address.

**Action:** rename the key in inventory before running the role:

```yaml
patroni_vip_engine: keepalived   # was: vip_manager: keepalived
```

## 1.0.2 (2026-06-08)

### Potentially breaking — `netbox_device_platform` default is now empty

The role default for `netbox_device_platform` changed from a hardcoded
`ubuntu-24-04-lts` to `""`. When empty, it now falls back to
`instance_platform_preset` (the OS preset the VM was built from), so the
registered NetBox platform tracks the deployed OS automatically.

**What you'll see:** VMs registered without an explicit `netbox_device_platform`
now get `instance_platform_preset` (default `fedora-coreos`) instead of
`ubuntu-24-04-lts`.

**Action:** if you depended on the old `ubuntu-24-04-lts` default, set it
explicitly in inventory:

```yaml
netbox_device_platform: ubuntu-24-04-lts
```

Resolution order (most specific wins; empty falls through): per-VM
`item.platform` → `netbox_device_platform` → `instance_platform_preset`.

## 0.1.x → 0.2.0

### Breaking — path and role renames

| Before | After | Action |
|---|---|---|
| `roles/service_preflight/` | `roles/preflight/` | Auto-handled if you use `mongodb` / `patroni` roles (meta deps updated). Only affects custom playbooks that call the preflight role by its old name. |
| `playbooks/deploy_full.yml` | `playbooks/deploy.yml` | Update any wrapper scripts or CI pipelines that invoke the old filename. The FQCN form `ansible-playbook startechnica.infra.deploy` also works once the collection is installed. |
| `playbooks/tasks/*.yml` | `roles/common/tasks/*` | Playbooks that referenced `include_tasks: tasks/inherit_localhost_vars.yml` must switch to `include_role: { name: common, tasks_from: inherit_localhost_vars }`. |

### Removed

- `playbooks/setup_ssh.yml` — cloud-init / ignition plant the default user's
  SSH key at first boot, so bootstrapping SSH separately is no longer needed.
  If you relied on it for retrofit scenarios, keep a local copy or adapt the
  three-line `authorized_key` task.
- `playbooks/setup_docker.yml` — equivalent to `deploy.yml --tags provision,docker`.
- `playbooks/provision_vms.yml` / `playbooks/provision_from_netbox.yml` —
  equivalent to `deploy.yml --tags provision`.

### New capabilities (opt-in, default off)

- **`firewall` role** — set `firewall_enabled: true` to enable UFW/firewalld
  policy management.
- **`routeros` role** — set `routeros_enabled: true` plus RouterOS creds to
  populate a MikroTik address-list with cluster IPs.
- **`routeros_prune_stale: true`** — additionally remove orphan entries when
  a node is dropped from `instances`. Off by default (safety).

### Target-side Python venv

`preflight` now creates `/opt/ansible-venv` on every target and switches
`ansible_python_interpreter` to it. No-op for fresh deployments. On
upgrades: `pymongo`, `psycopg2-binary`, `docker` Python packages that were
previously installed to system Python are now isolated in the venv.

**What you'll see:** the first run after upgrade creates the venv
(~30 seconds). Subsequent runs are unchanged. The system-Python installs
are not removed — you can clean them up later if desired.

**Why it matters:** Ubuntu 24.04+ and other PEP-668 distros refuse system
`pip install`. The venv avoids this entirely.

### Patroni `vip-manager` dependency fix

`vip-manager.depends_on` changed from `[patroni]` to `[etcd]`. Matches reality
— vip-manager watches the leader key in etcd, not Patroni's REST API. The
existing deployment won't break; but a fresh container restart will use the
new (correct) dependency.

### Ignition multi-distro

`ignition_flavour` is now a first-class input (default `fcos`). Existing FCOS
inventories are unaffected — same defaults. To try Flatcar Container Linux,
RHCOS, or openSUSE MicroOS, see
[roles/instance_ignition/README.md#flavor-support](../roles/instance_ignition/README.md).

### NetBox service registration is in-role

Previously, `deploy.yml` had a Stage 7 that registered MongoDB/Patroni service
ports in NetBox from localhost. That logic moved into the roles themselves
(runs at the end of `init_cluster.yml` / `start.yml`). Net effect: standalone
day-2 playbooks (`install.yml`, `restart.yml`, etc.) now also register
services, which was an earlier gap.

### Python interpreter validation-time safety

`roles/patroni/defaults/main.yml:vip_manager_arch` now uses
`ansible_architecture | default('x86_64')` to survive arg-spec validation
when facts haven't been gathered yet. No inventory changes needed; fixes
`'ansible_architecture' is undefined` errors on some edge-case play orderings.

## Upgrade recipe (any version)

1. **Back up** — run `ansible-playbook playbooks/mongodb/backup.yml` and
   `playbooks/patroni/backup.yml` before anything else.
2. **Read the changelog** — check the entries above your current version in
   [../CHANGELOG.md](../CHANGELOG.md).
3. **Update the collection**:
   ```bash
   ansible-galaxy collection install startechnica.infra --upgrade
   ```
4. **Refresh Python deps**:
   ```bash
   source .venv/bin/activate
   pip install --upgrade -r requirements.txt
   ```
5. **Run `--tags validate`** first — catches inventory incompatibilities
   before any side effects:
   ```bash
   ansible-playbook startechnica.infra.deploy -i inventories/<inv>.yml --tags validate
   ```
6. **Dry-run** in check mode:
   ```bash
   ansible-playbook startechnica.infra.deploy -i inventories/<inv>.yml --check --diff
   ```
7. **Apply** during your maintenance window.
8. **Verify** via cluster status + backup-verify:
   ```bash
   ansible-playbook playbooks/mongodb/status.yml -i inventories/<inv>.yml
   ansible-playbook playbooks/mongodb/verify-backup.yml -i inventories/<inv>.yml
   ansible-playbook playbooks/patroni/status.yml -i inventories/<inv>.yml
   ```

## When things go sideways

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for known failure modes and
remediation steps, and [../CHANGELOG.md](../CHANGELOG.md) under "Fixed" for
bugs that may have affected your previous version.
