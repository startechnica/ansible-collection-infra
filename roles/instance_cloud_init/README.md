# instance_cloud_init

Cloud-init phases for [instance](../instance/). Not a standalone role —
it's a sub-role invoked per-VM from `instance/tasks/configure_all.yml` via
`include_role: tasks_from:` inside a loop over `instances_to_create`.

## Phases (entry points)

| Entry file | Purpose |
|---|---|
| `tasks/inject.yml` | Writes `guestinfo.userdata` + `.metadata` + OVF `user-data` vapp properties to the VM. |
| `tasks/poweron.yml` | Powers the VM on **without** vSphere guest customization (cloud-init reads guestinfo on first boot). |
| `tasks/post_provision.yml` | Runs per-VM shell commands via VMware Tools (optional; uses `item.post_commands`). |
| `tasks/cleanup.yml` | Clears guestinfo after successful boot so subsequent boots don't re-run cloud-init. |

## Expected inputs (from parent role context)

- `item` — current VM dict from the loop (`name`, `networks`, `post_commands`, `cloud_init`, …)
- `vcenter` — vCenter connection dict
- `instance_datacenter`, `instance_folder`
- `cloud_init` — cloud-init config (`package_update`, `package_upgrade`)
- `instance_user_*` — flat per-field defaults (`instance_user_name`, `instance_user_password`, `instance_user_shell`, `instance_user_ssh_authorized_keys`, …)
- `instance_domain_name`, `instance_dns_servers`, `instance_timezone`, `instance_networks` (gateway4/gateway6/mtu)
- `instance_runcmds`, `instance_packages`, `instance_extra_users`, `instance_post_commands`

## Templates

- [templates/cloudinit-user-data.yml.j2](templates/cloudinit-user-data.yml.j2)
- [templates/cloudinit-metadata.yml.j2](templates/cloudinit-metadata.yml.j2)

## Role outputs

Each phase updates a cumulative `cloud_init_status` dict (set via `set_fact`),
keyed by VM name:

```yaml
cloud_init_status:
  <vm_name>:
    injected: true             # set after inject.yml
    userdata_bytes: 1234       # size of rendered user-data
    metadata_bytes: 456        # size of rendered metadata
    powered_on: true           # set after poweron.yml
    cleaned_up: true           # set after cleanup.yml
```

Other roles can check e.g. `cloud_init_status[item.name].injected | default(false)`
to confirm a phase ran without re-triggering it.

## Gotchas

- **Do not add `customization:` to the poweron task.** vSphere's built-in
  sysprep-style customization runs before cloud-init and marks the VM as
  customized, which makes cloud-init skip user-data.
- **`ssh_authorized_keys: null`** from the user-data template crashes the
  `cc_users_groups` cloud-init module. The template already guards against
  this by only emitting the key when the list is non-empty.
