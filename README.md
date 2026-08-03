# startechnica.infra

Ansible collection for deploying stateful data services on VMware vCenter with
NetBox as source of truth. Ships:

- **instance** — provision VMs from vCenter content library (orchestrator)
  - **instance_cloud_init** — cloud-init phase sub-role (inject/poweron/cleanup)
  - **instance_ignition** — ignition phase sub-role (prepare/render/inject/poweron/cleanup)
- **netbox_lookup / netbox_register** — query and register VMs in NetBox
- **mongodb** — sharded or replica-set MongoDB with TLS (x509 auth), exporter, backups (local + S3), PITR
- **patroni** — HA PostgreSQL with etcd, HAProxy, PgBouncer, vip-manager, WAL-G backups, PITR
- **grafana_alloy** — Grafana Alloy collector (logs→Loki, metrics→Prometheus, traces→Tempo); docker or podman
- **preflight** — shared cluster preflight (venv, Docker, RAM, kernel modules)
- **common** — reusable utility tasks (SSH probe, host-group build, localhost var inherit, inventory validator)
- **firewall** — declarative UFW / firewalld policy (opt-in)
- **routeros** — push VM IPs + VIP to a MikroTik address-list (opt-in)
- **vcenter_sync** — mirror vCenter inventory into NetBox

## Quick start

```bash
# 1. Install the collection
ansible-galaxy collection install startechnica.infra

# 2. Copy a starting inventory and edit it (vCenter creds, VM list, service tags)
cp <collection-path>/examples/inventory.minimal.yml inventories/mysite.yml
$EDITOR inventories/mysite.yml

# 3. Encrypt the secrets
ansible-vault encrypt inventories/group_vars/all/vault.yml

# 4. Validate, then run
ansible-playbook startechnica.infra.deploy -i inventories/mysite.yml --tags validate
ansible-playbook startechnica.infra.deploy -i inventories/mysite.yml --ask-vault-pass
```

See [examples/](examples/) for reference inventories, scheduling patterns,
and a complete end-to-end walkthrough.

## Requirements

- Python 3.12+ (earlier versions work but some deps break on 3.13+)
- Ansible 2.19+ (ansible-core)
- `pip`, `git`, and network access to vCenter + NetBox + any backup S3 endpoint

Python libs and Ansible collections install via `pip install -r requirements.txt`
and `ansible-galaxy collection install -r requirements.yml` (covered under
[Setup](#setup) below). Key transitive deps worth knowing about:

- **`ansible.utils`** (collection) — IP validation filters (`ipv4` / `ipv6`)
  used by `instance` and `common` validators.
- **`passlib`** (Python) — required by Ansible's `password_hash` filter,
  which the Ignition / Butane templates use to render hashed passwords for
  the FCOS `core` user.

## Setup

Clone and enter the collection directory:

```bash
cd /path/to/startechnica/infra
```

### 1. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

> Skip `source` on every shell if you prefer — call the venv binaries directly
> via `.venv/bin/python`, `.venv/bin/pip`, `.venv/bin/ansible-playbook`.

### 2. Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This pulls `pyvmomi`, `cryptography`, `psycopg2-binary`, `pymongo`, `docker`,
`requests`, and the other libraries Ansible modules in this collection need
at controller-side. Version floors are set to avoid known Python 3.12
incompatibilities (e.g. old `pyvmomi` using the removed `ssl.wrap_socket`).

### 3. Install Ansible dependencies

```bash
ansible-galaxy collection install -r requirements.yml
ansible-galaxy role install -r requirements.yml
```

### 4. Verify the environment

```bash
python -c "import pyVmomi, cryptography, psycopg2, pymongo, docker, requests; print('all ok')"
ansible-playbook --version
ansible-galaxy collection list community.vmware community.postgresql community.mongodb
```

## Usage

### Validate an inventory before deploying

```bash
ansible-playbook playbooks/deploy.yml -i inventories/<your-inventory>.yml --tags validate
```

Fails fast on missing/inconsistent configuration. Stage 0 of `deploy.yml`
also runs this on every invocation (tag `always`) — use `--tags validate`
to run *only* the checks.

### Full-stack install

Provisions VMs → installs Docker → deploys any service tagged in the inventory's
`instance_tags` list (`mongodb`, `patroni`, or both).

```bash
# From source tree
ansible-playbook playbooks/deploy.yml -i inventories/<your-inventory>.yml --ask-vault-pass

# Or via FQCN when installed as a collection (ansible-galaxy collection install startechnica.infra)
ansible-playbook startechnica.infra.deploy -i inventories/<your-inventory>.yml --ask-vault-pass
```

Scope via tags:

```bash
--tags netbox     # NetBox query only (dry-run)
--tags provision  # Provision VMs + register in NetBox
--tags docker     # Install Docker
--tags mongodb    # MongoDB only (Docker + mongodb role)
--tags patroni    # Patroni only (Docker + patroni role)
```

### Single-service playbooks

```bash
# MongoDB
ansible-playbook playbooks/mongodb/install.yml       -i inventories/<inv>.yml
ansible-playbook playbooks/mongodb/backup.yml        -i inventories/<inv>.yml
ansible-playbook playbooks/mongodb/verify-backup.yml -i inventories/<inv>.yml
ansible-playbook playbooks/mongodb/restart.yml       -i inventories/<inv>.yml
ansible-playbook playbooks/mongodb/add-node.yml      -i inventories/<inv>.yml -e target_node=<host>
ansible-playbook playbooks/mongodb/remove-node.yml   -i inventories/<inv>.yml -e target_node=<host>
ansible-playbook playbooks/mongodb/pitr.yml          -i inventories/<inv>.yml \
  -e backup_path=/opt/mongodb/backups/mongodump_YYYYMMDD_HHMMSS \
  -e target_time='2026-04-23T14:30:00Z'
ansible-playbook playbooks/mongodb/uninstall.yml     -i inventories/<inv>.yml \
  -e mongodb_destroy_prune=true

# Patroni
ansible-playbook playbooks/patroni/install.yml        -i inventories/<inv>.yml
ansible-playbook playbooks/patroni/status.yml         -i inventories/<inv>.yml
ansible-playbook playbooks/patroni/health.yml         -i inventories/<inv>.yml
ansible-playbook playbooks/patroni/backup.yml         -i inventories/<inv>.yml
ansible-playbook playbooks/patroni/verify-backup.yml  -i inventories/<inv>.yml
ansible-playbook playbooks/patroni/restart.yml        -i inventories/<inv>.yml
ansible-playbook playbooks/patroni/switchover.yml     -i inventories/<inv>.yml -e target_node=<host>
ansible-playbook playbooks/patroni/renew-certs.yml    -i inventories/<inv>.yml
ansible-playbook playbooks/patroni/rotate-passwords.yml -i inventories/<inv>.yml
ansible-playbook playbooks/patroni/add-node.yml       -i inventories/<inv>.yml -e target_node=<host>
ansible-playbook playbooks/patroni/remove-node.yml    -i inventories/<inv>.yml -e target_node=<host>
ansible-playbook playbooks/patroni/pitr.yml           -i inventories/<inv>.yml \
  -e target_time='2026-04-23 14:30:00+00'
ansible-playbook playbooks/patroni/uninstall.yml      -i inventories/<inv>.yml -e prune=true
```

### Patroni connection topology

Clients connect to the cluster VIP, never to a node directly. The request path is:

```
client
  │
  ▼
vip-manager        (floats the VIP to whichever node Patroni reports as leader)
  │
  ▼
HAProxy            (:5432 primary / :5433 replicas — health-checks Patroni's
  │                 REST API GET /primary | /replica to route to the live role)
  ▼
PgBouncer          (:6543 — connection pooling, session mode)
  │
  ▼
Patroni → PostgreSQL  (:55432 — managed Postgres + streaming replication)
```

Because HAProxy uses `on-marked-down shutdown-sessions`, a leader change (or a
health-check flap) force-closes live sessions, so clients must tolerate a
dropped connection and reconnect. etcd is the DCS backing Patroni's leader
election; vip-manager watches the same etcd leader key to move the VIP.

## Inventory shape

See [inventories/](inventories/) for working examples. Minimum keys per inventory
under `cloud_init.vars`:

```yaml
# Infrastructure
instance_cluster_name: <vcenter-cluster-name>
instance_disks:
  size_gb: 40
  type: thin
  datastore: <vcenter-datastore>
portgroup_name: <dvswitch-portgroup>           # role-level NIC fallback
portgroup_dvswitch_name: <dvswitch-name>
instance_folder: <vcenter-vm-folder>

# Per-NIC defaults — merged into every entry of every VM's `networks:`
instance_networks:
  gateway4: "<gateway-ip>"
  mtu: 1500
  # gateway6: "<v6-gateway>"   # add for IPv6 dual-stack

# Sizing
instance_cpu: 4
instance_memory_mb: 8192
instance_disks:
  size_gb: 160
  type: thin

# VM list — `hostname` is required (guest OS hostname); `name` is optional
# (vCenter VM display name; defaults to `hostname`). Each VM has a
# `networks:` list. Per-NIC `name` falls back to `portgroup_name`;
# gateway4/gateway6/mtu fall back to `instance_networks`. `ipv4` may be a
# CIDR ("10.0.0.10/24"), the literal "dhcp", or absent.
instances:
  - hostname: host1-db
    networks:
      - ipv4: 10.0.0.10/24
  - hostname: host2-db
    networks:
      - ipv4: 10.0.0.11/24
  - hostname: host3-db
    networks:
      - {}   # ipv4 enriched from NetBox into networks[0]

# Which services to deploy (drives deploy.yml routing)
instance_tags:
  - mongodb
  - patroni

# Container runtime — three independent knobs (see roles/instance/defaults/main.yml
# for the full truth table). The selected `container_engine` must have its
# install/enable toggle on, otherwise mongodb/patroni end_role themselves.
container_engine: docker          # docker | podman
docker_enabled: true              # install docker-ce on cloud-init hosts
                                  # (FCOS hosts get it layered via Ignition)
podman_enabled: false             # enable podman + Quadlets (ignition path only)

# NetBox
netbox_enabled: true
netbox_lookup_enabled: true
netbox_device_role: database
netbox_device_platform: ubuntu-24-04-lts   # optional; empty → uses instance_platform_preset

# MongoDB (required when instance_tags contains 'mongodb')
mongodb_admin_password: "..."  # or "{{ vault_mongodb_admin_password }}"

# Patroni (required when instance_tags contains 'patroni')
patroni_scope: <unique-scope>   # e.g. projectname-pg
patroni_vip_address: "<unused-ip-on-subnet>"    # floating IP for leader
# postgresql_postgres_password: ""         # leave empty to auto-generate

# S3 backups (optional; shared by mongodb + patroni)
s3_endpoint: "https://s3.example.com"
s3_bucket: "backups"
s3_access_key: "..."
s3_secret_key: "..."
```

NetBox connection + vCenter credentials are best kept in
[group_vars/all/](inventories/group_vars/all/) (encrypted with `ansible-vault`).

## Further reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the roles, playbooks, and stages fit together.
- [docs/UPGRADING.md](docs/UPGRADING.md) — version-to-version migration notes, including variable renames and breaking changes.
- [docs/SECRETS.md](docs/SECRETS.md) — vault layout, secret rotation, and where credentials live.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — deeper triage for the issues called out below.

## Troubleshooting

- **`ssl.wrap_socket` error** — pyvmomi too old for Python 3.12. Fix:
  `pip install --upgrade 'pyvmomi>=8.0.3'` in the active venv.
- **`Failed to import the required Python library (requests)`** — missing
  runtime dep. Fix: `pip install -r requirements.txt` in the active venv.
- **`/home/.../python3.12` wrong Python** — Ansible is using a venv but pip
  wasn't. Always install with `./.venv/bin/pip install ...` or activate the
  venv first.
- **Deploy fails at Stage 0 (validate)** — check the fail message; it
  names the missing or inconsistent variable.
- **NIC shows "(disconnected)" / VM gets no IP on a distributed switch** — the
  role auto-corrects this: content-library OVFs create the NIC with a
  standard-vSwitch backing, and the provision rebinds it to the distributed
  portgroup via `vmware_guest_network`, then asserts it bound. If the assert
  *fails* (`portgroup_key … (null)`), the portgroup or dvswitch name is wrong
  (both are **case-sensitive**), or the VM's ESXi host isn't a member of that
  DVS — verify names in vCenter → Networking against `portgroup_name` /
  `portgroup_dvswitch_name`.
- **OVA import fails with "IO error during transfer … Pipe closed"** — a
  transient vCenter host→datastore (NFC) transfer drop, not a config error. The
  import auto-retries (`content_library_import_retries`, default 3) and cleans
  the partial item between attempts. If it fails *all* attempts, the cause is
  structural (firewall/proxy closing long HTTPS transfers, or a datastore issue)
  — raise `content_library_import_timeout`, or pin an already-imported OVA via
  `content_library_item_name` to skip the transfer.
- **FCOS metadata fetch 404s on `streams/.json`** — `instance_platform_preset`
  is unset (or a non-FCOS preset) while ignition auto-import is on, so the stream
  channel resolved empty. Set `instance_platform_preset: fedora-coreos` (or the
  correct preset) in the inventory.
- **Patroni stage fails on `patroni_vip_address` undefined** — add `patroni_vip_address:` to the inventory's
  Patroni section or set `vip_manager: "none"` to skip.
- **MongoDB preflight fails on kernel 6.19–7.0.13** — MongoDB 8 crashes on
  startup on a *bounded range* of Linux kernels, **6.19 through 7.0.13**, from a
  vendored-TCMalloc/rseq ABI bug. Linux **7.0.14+ resolves it kernel-side**, so
  a host on 7.0.14+ (or `< 6.19`) passes preflight. The fix is in the kernel,
  not MongoDB — Mongo's vendored TCMalloc is still unpatched through 8.2, so
  upgrading Mongo alone does NOT escape the range. The kernel bump can land
  *within* a single FCOS major (e.g. FCOS `43.20260217.3.1` = kernel 6.18 OK,
  `43.20260413.3.2` = kernel 6.19 broken), so pinning the FCOS major is not
  enough. Either upgrade to a build with kernel `>= 7.0.14`, or pin one with
  kernel `< 6.19`. See the [mongodb role README](roles/mongodb/README.md#gotchas)
  for the full table and the `mongodb_skip_kernel_check` bypass.

## License

Copyright &copy; 2026 Startechnica

Released under the [MIT License](LICENSE). The software is provided "as is",
without warranty of any kind, express or implied.
