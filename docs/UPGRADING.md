# Upgrading

The collection follows [Semantic Versioning](https://semver.org/) with one
caveat: **pre-1.0 minor bumps may contain breaking changes**. Read the release
notes in [../CHANGELOG.md](../CHANGELOG.md) before upgrading; this file
documents the migration steps for breaking changes.

## 1.0.3 (unreleased)

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
