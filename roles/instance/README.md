# VMware vCenter VM Provisioning

Ansible role and playbooks for automated VM provisioning on VMware vCenter using content library templates.

## Requirements

- Ansible 2.19+
- Python 3.12+
- Collections:
  - `community.vmware` >= 6.2.0
  - `vmware.vmware` >= 2.7.0
  - `ansible.utils`

Install collections:

```bash
ansible-galaxy collection install community.vmware vmware.vmware ansible.utils
```

## Role Layout

```
roles/instance/
├── defaults/main.yml
├── meta/{main.yml,argument_specs.yml}
├── tasks/
│   ├── main.yml                # Orchestration (preflight → validate → deploy → configure)
│   ├── validate/
│   │   ├── vcenter.yml         # vCenter credentials assert (runs first)
│   │   ├── input.yml           # Pure-data input checks (hostname, NICs, disks)
│   │   ├── vms.yml             # vCenter folder lookup → instances_to_create/skip
│   │   ├── template.yml        # Content library template lookup
│   │   ├── datastore.yml       # Datastore lookup
│   │   ├── network.yml         # Portgroup discovery
│   │   └── role.yml            # vCenter permission role lookup
│   ├── validate.yml            # Includes validate_{template,datastore,network,folder,role,vms}
│   ├── validate_*.yml          # Per-resource validation
│   ├── network.yml             # Create missing portgroups (standard or distributed)
│   ├── folder.yml              # Create VM folder if missing
│   ├── deploy_all.yml          # Deploy each VM (dispatches on instance_init_type)
│   ├── configure_all.yml       # Per-VM configure → inject → power on → verify → cleanup
│   ├── create_vm/              # Shared create-VM subtasks
│   │   ├── deploy_from_library.yml
│   │   ├── deploy_ovf.yml
│   │   ├── deploy_vm_template.yml
│   │   ├── configure_hardware.yml
│   │   ├── verify.yml
│   │   └── detach_cdrom.yml    # Post-cloud-init: detach OVF-env ISO
│   ├── ssh_config.yml          # Apply sshd overrides via VMware Tools
│   └── permission.yml          # vCenter permission assignment (optional)
└── templates/                  # (cloud-init / ignition templates live in sub-roles)
```

The `cloud-init` vs `ignition` first-boot injection is delegated to two dedicated
sub-roles — [instance_cloud_init](../instance_cloud_init/) and
[instance_ignition](../instance_ignition/) — invoked via
`include_role: tasks_from:` from `configure_all.yml`.

## Usage

The role is designed to be invoked from the collection's orchestration playbooks:

```bash
# Full stack (VMs + docker + services):
ansible-playbook playbooks/deploy.yml -i inventories/<your-inventory>.yml --ask-vault-pass

# Provision VMs only:
ansible-playbook playbooks/deploy.yml -i inventories/<your-inventory>.yml --ask-vault-pass --tags provision

# Or via FQCN when installed as a collection:
ansible-playbook startechnica.infra.deploy -i inventories/<your-inventory>.yml --ask-vault-pass --tags provision
```

Or included directly in your own playbook:

```yaml
- hosts: localhost
  gather_facts: true
  roles:
    - role: startechnica.infra.instance
```

Inventory shape (minimum):

```yaml
# inventories/<name>.yml
all:
  hosts:
    localhost: {ansible_connection: local}
  vars:
    vcenter_connect:
      hostname: vcenter.example.com
      username: administrator@vsphere.local
      password: "{{ vault_vcenter_password }}"
      validate_certs: false
    instance_datacenter: <your-vcenter-datacenter>
    instance_cluster_name: <your-cluster>
    instance_folder: <your-project-folder>
    portgroup_name: <your-portgroup>
    portgroup_dvswitch_name: <your-dvswitch>
    instance_networks:
      gateway4: 10.0.0.1
      mtu: 1500

    content_library_name: ubuntu-images
    content_library_template: noble-server-cloudimg-amd64
    content_library_type: ovf

    instances:
      - hostname: myvm-01
        networks:
          - ipv4: 10.0.0.10/24
      - hostname: myvm-02
        networks:
          - ipv4: 10.0.0.11/24
```

vCenter credentials typically live under `inventories/group_vars/` encrypted with
ansible-vault — see [docs/SECRETS.md](../../docs/SECRETS.md) for the vault pattern.

## Deployment Workflow

The role executes the following steps:

1. **Preflight** — validate vCenter credentials, list VMs already present
2. **Validate** — check content library template, datastore, portgroups, VM folder, permission role
3. **Network** — create missing portgroups (standard or distributed vSwitch)
4. **Folder** — create VM folder if missing
5. **Deploy** — for each VM, clone from content library (OVF or vm-template)
6. **Configure** — per VM:
   - Configure hardware (CPU, memory, hw version, disk size, boot firmware)
   - Inject cloud-init or ignition via the matching sub-role
   - Power on and wait for guest to settle
   - Verify IP, hostname, tools status
   - Detach OVF-env CD-ROM ISO (cloud-init only)
   - Run post-provision commands (cloud-init only, via VMware Tools)
   - Apply sshd overrides from `instance_ssh_configs` (if set)
   - Clean up guestinfo data
7. **Permissions** — assign vCenter role permissions (if `permission.enabled`)

## Variable Reference

### Infrastructure

| Variable | Default | Description |
|---|---|---|
| `instance_datacenter` | `""` | vCenter datacenter name (required) |
| `instance_cluster_name` | `""` | Cluster name (for cluster deployments) |
| `vsphere_esxi_hostname` | `""` | ESXi host (for standalone deployments) |
| `instance_disks.datastore` | `""` | Project-level datastore (per-disk override via `disks[i].datastore`) |
| `instance_folder` | `""` | VM folder in vCenter |
| `instance_domain_name` | `id-central-1.compute.internal` | Domain appended to hostname |
| `instance_dns_servers` | `[8.8.8.8, 1.1.1.1]` | DNS servers for VMs |

> Set either `instance_cluster_name` or `vsphere_esxi_hostname`, not both.

### Content Library

| Variable | Default | Description |
|---|---|---|
| `content_library_name` | `""` | Content library name |
| `content_library_template` | `""` | Template name in content library |
| `content_library_type` | `ovf` | Template type: `ovf` or `vm-template` |

### Network

| Variable | Default | Description |
|---|---|---|
| `portgroup_name` | `""` | Role-level NIC fallback when a `networks[*]` entry omits `name:` |
| `portgroup_vlan_id` | `0` | VLAN ID (for creating new portgroups) |
| `portgroup_vswitch_name` | `vSwitch0` | Standard vSwitch (ESXi standalone) |
| `portgroup_dvswitch_name` | `""` | Distributed vSwitch name (cluster) |
| `instance_networks` | `{mtu: 1500}` | Per-NIC defaults dict merged into every entry of every VM's `networks:`. Keys: `gateway4`, `gateway6`, `mtu`. |

### VM Specs

| Variable | Default | Description |
|---|---|---|
| `instance_cpu` | `2` | Number of vCPUs |
| `instance_memory_mb` | `2048` | Memory in MB |
| `instance_disks` | `{size_gb: 40, type: thin}` | Per-disk defaults dict merged into every entry of every VM's `disks:` list. Keys: `size_gb`, `type` (`thin`/`thick`/`eagerzeroedthick`), `datastore`, `controller_type`, `controller_number`, `unit_number`, `disk_mode`. |
| `vsphere_hw_version` | `15` | Hardware version (only upgrades, never downgrades) |
| `instance_boot_firmware` | `efi` | Boot firmware: `efi` or `bios` |

### Hardware Version Map

Reference for ESXi compatibility:

| ESXi Version | Hardware Version |
|---|---|
| ESXi 8.0 U2+ | 21 |
| ESXi 8.0 | 20 |
| ESXi 7.0 U2+ | 19 |
| ESXi 7.0 | 17 |
| ESXi 6.7 U2 | 15 |
| ESXi 6.7 | 14 |
| ESXi 6.5 | 13 |

### Default User

| Variable | Default | Description |
|---|---|---|
| `instance_user_name` | `ubuntu` | Default OS username (`core` for FCOS) |
| `instance_user_ssh_authorized_keys` | `[]` | SSH public keys for the default user |
| `instance_user_sudo` | `ALL=(ALL) NOPASSWD:ALL` | Sudo config (cloud-init only) |
| `instance_user_shell` | `/bin/bash` | User shell (cloud-init only) |
| `instance_user_lock_passwd` | `true` | Lock password login (cloud-init only) |
| `instance_user_password` | `""` | Plain text password, auto-hashed SHA-512 (cloud-init only) |

When `password` is set (cloud-init):
- Password is automatically hashed (SHA-512)
- SSH password authentication is enabled
- Account is unlocked

### Post-Provision Commands

| Variable | Default | Description |
|---|---|---|
| `instance_post_commands` | `[]` | Shell commands to run after VM is ready |

Commands are executed via VMware Tools (`vmware_vm_shell`) as the `default_user`.

### Permissions

| Variable | Default | Description |
|---|---|---|
| `vcenter_permission_enabled` | `false` | Enable VM permission assignment |
| `vcenter_permission_role` | `""` | vCenter role name |
| `vcenter_permission_propagate` | `false` | Propagate permission to children |
| `vcenter_principal_user` | `""` | User/group to assign permission |
| `vcenter_principal_is_group` | `false` | Whether principal is a group |

### Debug

| Variable | Default | Description |
|---|---|---|
| `debug` | `false` | Show detailed debug output |
| `debug_validation` | `false` | Show validation step details |

## Per-VM Overrides

Each VM in the `instances` list has a required `networks: []` and may override
role-level defaults via per-item dict keys (Ansible idiom: per-item keys stay
short — they're dict-scoped, not in the global var namespace).

```yaml
instances:
  - hostname: my-vm-01                # required (guest OS hostname)
    # name: vcenter-display-name      # optional; defaults to hostname
    networks:
      - ipv4: 10.0.0.10/24            # uses portgroup_name + instance_networks.gateway4

    # Optional per-VM overrides (dict-scoped, no `instance_` prefix needed)
    cpu: 8
    memory_mb: 16384
    disks:
      - size_gb: 500              # primary disk
      # - size_gb: 1000           # add a second disk (multi-disk path)
      #   type: thin
      #   datastore: datastore02  # per-disk datastore override
    vsphere_hw_version: 19
    boot_firmware: efi

    # Cloud-init extras
    cloud_init:
      packages:
        - curl
        - git
      runcmd:
        - echo "Hello" > /tmp/hello

    # Per-VM post-provision commands
    post_commands:
      - systemctl enable postgresql
      - systemctl start postgresql

  # Multi-NIC + dual-stack + DHCP example with explicit vCenter name
  - hostname: my-gw-01                # guest sees this in /etc/hostname
    name: gw01-asset-01234            # vCenter UI shows asset tag
    networks:
      - name: pg-mgmt                 # explicit portgroup overrides portgroup_name
        ipv4: 10.0.0.20/24
        ipv6: 2001:db8::20/64
      - name: pg-data
        ipv4: dhcp                    # DHCPv4 — `ipv6: dhcp6` also supported
        mtu: 9000                     # jumbo frames for storage
```

## Examples

### Cluster Deployment with Docker

```yaml
# inventories/myproject/group_vars/database.yml
instance_cluster_name: ProductionCluster
instance_disks:
  size_gb: 160
  type: thin
  datastore: vsanDatastore

content_library_name: "ubuntu-images"
content_library_template: "noble-server-cloudimg-amd64"
content_library_type: "ovf"

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

instance_user_password: mypassword

instance_post_commands:
  - "apt-get update && apt-get upgrade -y"
  - "curl -fsSL https://get.docker.com | sh"

instances:
  - hostname: app-01
    networks:
      - ipv4: 10.10.0.50/24
  - hostname: app-02
    networks:
      - ipv4: 10.10.0.51/24
  - hostname: app-03
    networks:
      - ipv4: 10.10.0.52/24
```

### Standalone ESXi Deployment

```yaml
# inventories/myproject/group_vars/database.yml
vsphere_esxi_hostname: "192.168.1.100"
instance_disks:
  size_gb: 40
  type: thin
  datastore: local-datastore

content_library_name: "ubuntu-images"
content_library_template: "noble-server-cloudimg-amd64"
content_library_type: "ovf"

portgroup_name: VM Network
portgroup_vlan_id: 0
portgroup_vswitch_name: vSwitch0

instance_folder: "TestVMs"
instance_networks:
  gateway4: 192.168.1.1
  mtu: 1500

instances:
  - hostname: test-vm-01
    networks:
      - ipv4: 192.168.1.50/24
```

### Override Variables at Runtime

```bash
ansible-playbook playbooks/deploy.yml \
  -i inventories/myproject.yml \
  --tags provision -e "debug=true debug_validation=true"
```

## Variable Precedence

From lowest to highest priority:

1. Role defaults (`roles/instance/defaults/main.yml`)
2. Inventory vars (`inventories/<name>.yml`)
3. Inventory `group_vars/` (`inventories/group_vars/*.yml`)
4. Command-line extras (`-e "key=value"`)

## Troubleshooting

### VM created but wrong CPU/memory/disk

High-risk dict variables in this collection (`instance_user_*`, `netbox_device_*`, `netbox_cluster_name`) were intentionally flattened to top-level scalars to avoid Ansible's shallow-dict-merge foot-gun (overriding `default_user: { password: ... }` would silently drop name/shell/sudo/etc.). Override individual `instance_user_*` fields freely; defaults handle the rest.

### Hardware version downgrade error

vSphere cannot downgrade hardware versions. If the template deploys at version 17, setting `vsphere_hw_version: 15` will be skipped. Set `vsphere_hw_version` equal to or higher than the template version, or leave it unset.

### Content library template not found

Verify the content library name and template name match exactly. Run with `debug_validation: true` to see what was found.

### Network portgroup not found

For cluster deployments, the role checks distributed portgroups. For standalone ESXi, it checks standard portgroups. Ensure your `portgroup_name` matches the exact portgroup name in vCenter.

### Cloud-init not applying (hostname/IP wrong)

The role uses three methods: vApp properties, guestinfo, and guest customization. If the template doesn't support one method, another should work. Ensure `open-vm-tools` is installed in the template.

### Boot firmware not changing

Boot firmware must be set while the VM is powered off. The role handles this automatically. If the template is BIOS-only, switching to EFI may cause boot issues — use a template that supports EFI.

## Role outputs

Downstream roles consume these facts (set via `set_fact` at the end of the run):

### `_provisioned_vm_facts` — dict keyed by VM name

```yaml
_provisioned_vm_facts:
  <vm_name>:
    instance_uuid: "502c..."         # vCenter VM UUID (stable across renames)
    mac_address: "00:50:56:aa:..."   # MAC of hw_eth0
    ipv4: "10.0.0.10"                # first IPv4 reported by VMware Tools
    ipv6: "fe80::..."                # first IPv6 (if any)
    hw_name: "my-vm-01"              # hostname as vCenter sees it
    hw_power_status: "poweredOn"
    guest_tools_status: "guestToolsRunning"
    folder: "/DC/vm/MyProject"
    cluster: "ProductionCluster"
    instance_datacenter: "DC"
```

Set by `create_vm/verify.yml`, populated once per VM after boot + VMware Tools reports in. Consumed by the `netbox_register` role.

### `vm_ssh_config_status` — what sshd overrides were applied (if any)

Set by [`tasks/ssh_config.yml`](tasks/ssh_config.yml) after SSH customisation
runs on each VM. Keyed by VM name:

```yaml
vm_ssh_config_status:
  <vm_name>:
    applied: true
    port: 2222
    directives: [PasswordAuthentication, PermitRootLogin]
    migrated_from_22: true
```

Runs when `instance_ssh_port` ≠ 22 OR `instance_ssh_configs` (role-level or
per-VM) has at least one key. Uses VMware Tools (no SSH to the guest required),
so it works even before `ansible_user` can connect on the new port.

**`ansible_port` is auto-propagated** — the `common` role's [build_host_groups.yml](../common/tasks/build_host_groups.yml)
and the `_setup.yml` files in `playbooks/mongodb/` and `playbooks/patroni/` read
`item.ssh_port` (falling back to `instance_ssh_port`, then `22`) when calling
`add_host`. Any SSH-based play that runs *after* instance — Docker install,
mongodb/patroni provision, rolling restarts, etc. — will connect on the custom
port automatically. The firewall role opens the port via the same
`ansible_port` derivation. [deploy.yml](../../playbooks/deploy.yml)'s stage 3.5
`wait_for` similarly uses `{{ ansible_port | default(22) }}`.

**Port migration safety** — when `instance_ssh_port` ≠ 22, the task runs a
two-phase migration: bind BOTH 22 and the new port → verify the new port is
reachable from the controller → drop 22. On verify failure, sshd stays on
both ports so the operator can still SSH in on 22 to diagnose (most likely
cause: OS-level firewall, SELinux not in permissive, or socket activation —
the task handles all three but can't predict every environment). Subsequent
runs detect the already-migrated state and skip the transitional phase.

Configuration example:

```yaml
# Role-level — applies to every VM
instance_ssh_port: 2222
instance_ssh_configs:
  PasswordAuthentication: "no"
  PermitRootLogin: "no"
  ClientAliveInterval: 300

# Per-VM override
instances:
  - name: bastion-01
    ipv4: 10.0.0.5
    ssh_port: 2223
    ssh_config:
      ClientAliveInterval: 600
```

### `instance_summary` — roll-up for reporting

```yaml
instance_summary:
  total: 3                   # total VMs in inventory `instances`
  created: [host3]    # newly created this run
  existing: [host1, host2]   # pre-existing, skipped
  facts: { ... }             # copy of _provisioned_vm_facts
```

Useful for play recap / audit logs.

### Sub-role outputs

- **instance_cloud_init** → `cloud_init_status` dict (see [its README](../instance_cloud_init/README.md))
- **instance_ignition** → `ignition_status` dict + `fcos_import_facts` (see [its README](../instance_ignition/README.md))