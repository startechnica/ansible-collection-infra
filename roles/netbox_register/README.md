# NetBox VM Registration

Ansible role for registering vCenter VMs into NetBox as virtual machines with interfaces, IP addresses, and vCenter UUID tracking.

## Requirements

- Ansible 2.19+
- Python 3.12+ with `pynetbox` installed
- Collections:
  - `netbox.netbox`
  - `community.vmware`

Install dependencies:

```bash
ansible-galaxy collection install netbox.netbox community.vmware
pip install pynetbox
```

## What It Does

For each VM in the `instances` list, the role:

1. **Fetches vCenter UUID** - Queries vCenter for the VM's `instance_uuid` and MAC address
2. **Ensures custom field** - Creates the `vcenter_uuid` custom field in NetBox (once per run)
3. **Creates or updates VM** - Looks up the VM by name + cluster; creates if missing, updates if existing
4. **Creates or updates interface** - Manages the primary network interface with MAC address from vCenter
5. **Assigns IP address** - Creates or reassigns the IP address to the VM's interface
6. **Sets primary IP** - Marks the IP as the VM's primary IPv4

## Quick Start

### 1. Configure NetBox Connection

Edit `group_vars/all/netbox.yml`:

```yaml
netbox_connect:
  url: "https://netbox.example.com"
  token: "your-api-token-here"
  validate_certs: false
```

Encrypt with ansible-vault:

```bash
ansible-vault encrypt group_vars/all/netbox.yml
```

### 2. Run Standalone

```bash
ansible-playbook playbooks/deploy.yml -i inventories/<inv>.yml --tags netbox
```

### 3. Run After Provisioning

The role runs automatically after `instance` in the collection's orchestration
playbooks (`deploy.yml`, `deploy_mongodb.yml`, `deploy_patroni.yml`). Minimal
custom wiring:

```yaml
- name: Provision and register VMs
  hosts: localhost
  gather_facts: true
  collections:
    - community.vmware
    - netbox.netbox
  roles:
    - startechnica.infra.instance
    - role: startechnica.infra.netbox_register
      when: netbox_connect.token | default('') | length > 0
```

NetBox registration is skipped if no token is configured.

## Variable Reference

### Connection (`netbox_connect`)

| Variable | Default | Description |
|---|---|---|
| `netbox_connect.url` | `""` | NetBox base URL (e.g. `https://netbox.example.com`) |
| `netbox_connect.token` | `""` | API token with VM create/update permissions |
| `netbox_connect.validate_certs` | `false` | Validate SSL certificates |

### Device Properties (`netbox_device_*`)

Flat variables, not a dict — Ansible's shallow dict merge silently drops
keys you don't re-declare on override, so each field is a top-level variable.

| Variable | Default | Description |
|---|---|---|
| `netbox_device_name` | `""` | NetBox DCIM device name for the underlying ESXi host. Falls back to `vsphere_esxi_hostname`. |
| `netbox_device_vm_status` | `active` | VM status: `active`, `staged`, `planned`, `decommissioning`, `offline` |
| `netbox_device_interface_name` | `ens192` | Primary network interface name |
| `netbox_device_platform` | `""` | Platform slug; empty → falls back to `instance_platform_preset`. Auto-created if missing (see `netbox_register_create`) |
| `netbox_device_role` | `""` | VM role slug. Auto-created if missing (see `netbox_register_create`) |
| `netbox_cluster_name` | `""` | NetBox Cluster name. Resolution: `item.cluster_name` → `netbox_cluster_name` → `instance_cluster_name`. Auto-created if missing |

### Auto-creating referenced objects

By default the role **creates the platform, device role, and cluster it
references** when they don't already exist in NetBox, so registration doesn't
fail on an unknown slug. Look-up-then-create only — an object that already
exists is never modified.

| Variable | Default | Description |
|---|---|---|
| `netbox_register_create` | `true` | Master toggle. Set `false` to require platform/role/cluster to pre-exist (NetBox authoritative). |
| `netbox_cluster_type` | `VMware vSphere` | Cluster type used when auto-creating a cluster (NetBox requires one); slug derived from the name. |
| `netbox_register_default_role_color` | `9e9e9e` | Hex color (no `#`) for an auto-created device role. |

> Site and tenant are **not** auto-created — those are usually org-authoritative,
> so a typo'd slug should fail loudly rather than spawn a junk record.

### Organization (`netbox_site_slug` / `netbox_tenant_slug`)

| Variable | Default | Description |
|---|---|---|
| `netbox_site_slug` | `""` | Site slug (must exist in NetBox) |
| `netbox_tenant_slug` | `""` | Tenant slug (must exist in NetBox) |

### Other Settings

| Variable | Default | Description |
|---|---|---|
| `instance_tags` | `[]` | Tags to apply to VMs |
| `netbox_custom_fields` | `{}` | Custom field key-value pairs |
| `netbox_update_existing` | `true` | Update existing VMs or skip them |

## Per-VM Overrides

Each VM in the `instances` list can override global NetBox settings:

```yaml
instances:
  - name: my-vm-01
    ipv4: 10.0.0.10/24

    # NetBox overrides
    netbox_status: staged
    netbox_platform: centos-9
    netbox_vm_role: web-server
    netbox_site: dc-jakarta-1
    netbox_tenant: engineering
    netbox_interface: eth0
    tags: ["production", "managed"]
    netbox_custom_fields:
      environment: production
```

## IP Address Format

IPs support CIDR notation with per-VM prefixes:

```yaml
instances:
  - name: vm-01
    ipv4: 10.0.0.10/24      # /24 subnet

  - name: vm-02
    ipv4: 172.16.0.10/22    # /22 subnet

  - name: vm-03
    ipv4: 192.168.1.10      # defaults to /24
```

## Duplicate VM Handling

The role identifies VMs by **name + cluster**. If a VM with the same name exists in the same cluster:

- The existing VM is **updated** (specs, custom fields, UUID)
- No duplicate is created

This makes the role safe to run multiple times (idempotent).

## What Gets Registered in NetBox

| NetBox Object | Source |
|---|---|
| VM name | `item.name` |
| VM status | `netbox_device_vm_status` or per-VM `netbox_status` |
| vCPUs | `item.cpu` or global `instance_cpu` |
| Memory | `item.memory_mb` or global `instance_memory_mb` |
| Disk | `item.disks[0].size_gb` or global `instance_disks.size_gb` (per-disk VirtualDisks registered separately from vCenter) |
| Cluster | `instance_cluster_name` or `vsphere_esxi_hostname` |
| Platform | `netbox_device_platform` or per-VM `netbox_platform` |
| Role | `netbox_device_role` or per-VM `netbox_vm_role` |
| Site | `netbox_site_slug` or per-VM `netbox_site` |
| Tenant | `netbox_tenant_slug` or per-VM `netbox_tenant` |
| Interface | `netbox_device_interface_name` or per-VM `netbox_interface` |
| MAC address | Fetched from vCenter (`hw_eth0.macaddress`) |
| IP address | `item.networks[0].ipv4` (with CIDR prefix) |
| Primary IPv4 | Set automatically from `item.networks[0].ipv4` |
| vCenter UUID | Fetched from vCenter (`instance_uuid`), stored as custom field |
| Tags | `instance_tags` or per-VM `tags` (merged with the global list) |
| Custom fields | `netbox_custom_fields` or per-VM `netbox_custom_fields` |

## Required Variables

These must be defined for the role to work (typically via `vars_files` in the playbook):

| Variable | Source | Purpose |
|---|---|---|
| `netbox_connect.url` | `group_vars/all/netbox.yml` | NetBox API endpoint |
| `netbox_connect.token` | `group_vars/all/netbox.yml` | NetBox API token |
| `vcenter_connect.hostname` | `group_vars/all/vcenter.yml` | vCenter for UUID lookup |
| `vcenter_connect.username` | `group_vars/all/vcenter.yml` | vCenter credentials |
| `vcenter_connect.password` | `group_vars/all/vcenter.yml` | vCenter credentials |
| `instance_datacenter` | `group_vars/all/common.yml` | vCenter instance_datacenter |
| `instances` | Inventory `group_vars` | List of VMs to register |

## Variable Precedence

From lowest to highest priority:

1. Role defaults (`roles/netbox_register/defaults/main.yml`)
2. Global group_vars (`group_vars/all/common.yml`)
3. Inventory group_vars (`inventories/{project}/group_vars/database.yml`)
4. Playbook vars_files (`group_vars/all/netbox.yml`)
5. Per-VM overrides (`item.netbox_*`)
6. Command-line extras (`-e "key=value"`)

**Note on flat variables:** `netbox_device_*` and `netbox_cluster_name` are
top-level variables, not a dict. This sidesteps Ansible's shallow-dict-merge
foot-gun (overriding a dict drops every key you don't re-declare). Override
just the fields you care about; the rest fall through to role defaults.

## Example: Full Inventory

```yaml
# inventories/myproject/group_vars/database.yml
instance_cluster_name: ProductionCluster
instance_disks:
  size_gb: 160
  type: thin
  datastore: vsanDatastore

portgroup_name: v100
portgroup_dvswitch_name: DSwitch-Production

instance_folder: "MyProject"
instance_networks:
  gateway4: 10.10.0.1
  mtu: 1500

instance_cpu: 4
instance_memory_mb: 8192
instance_disks:
  size_gb: 160
  type: thin

netbox_device_role: "database"
netbox_device_platform: "ubuntu-24-04-lts"   # or leave empty to inherit instance_platform_preset

netbox_site_slug: "dc-jakarta-1"
netbox_tenant_slug: "engineering"

instance_tags:
  - production
  - managed

instances:
  - name: db-01
    networks:
      - ipv4: 10.10.0.50/24

  - name: db-02
    networks:
      - ipv4: 10.10.0.51/24
    tags: ["production", "primary"]

  - name: db-03
    networks:
      - ipv4: 10.10.0.52/24
    netbox_vm_role: "database-replica"
```

## Troubleshooting

### `pynetbox` not found

Install in the Python environment Ansible uses:

```bash
/path/to/venv/bin/pip install pynetbox
```

### "More than one result returned for virtual_machine"

Duplicate VMs exist in NetBox with the same name. The role handles this by looking up by name + cluster, but if duplicates already exist from prior runs, clean them up manually in the NetBox UI.

### Custom field creation fails

The API token needs permission to create custom fields. Ensure the token has the `extras.add_customfield` permission, or create the `vcenter_uuid` custom field manually in NetBox under **Customization > Custom Fields**.

### VM not found in vCenter

The role queries vCenter by VM name and instance_datacenter. Ensure:
- `instance_datacenter` matches your vCenter instance_datacenter name
- The VM exists and is visible to the vCenter user
- `vcenter.*` credentials are correct

### Override only the field you care about

`netbox_device_*` and `netbox_cluster_name` are flat top-level variables, so
overriding `netbox_device_role` (for example) does NOT affect
`netbox_device_platform` or `netbox_device_interface_name` — they continue to
fall back to the role defaults. (This deliberately avoids the dict-merge
foot-gun where overriding a dict silently drops every key you don't re-declare.)
