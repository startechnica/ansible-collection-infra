# instance_ignition

Ignition phases for [instance](../instance/). Supports Fedora CoreOS,
Flatcar Container Linux, Red Hat CoreOS (RHCOS), and openSUSE MicroOS —
pick via `instance_platform_preset` (see *Preset support* below).

Not a standalone role — sub-role invoked from `instance/tasks/main.yml`
via `include_role: tasks_from:`.

## Preset support

`instance_platform_preset` (parent-role var) selects a row in
`_platform_map` (`roles/common/vars/main.yml`). The row's `ignition:`
sub-dict supplies every flavour-varying knob; callers read it as
`_platform_preset.ignition.<field>` (published as a host fact by
`common/tasks/resolve_platform_preset.yml`).

| Preset | Butane variant | Spec version | Template prefix | Default user | Default channel | Channels | Metadata URL |
|---|---|---|---|---|---|---|---|
| `fedora-coreos` | `fcos` | `1.5.0` | `fedora-coreos` | `core` | `stable` | `stable`, `testing`, `next` | `https://builds.coreos.fedoraproject.org/streams/{channel}.json` |
| `flatcar` | `flatcar` | `1.1.0` | `flatcar-production-vmware` | `core` | `stable` | `stable`, `beta`, `alpha`, `lts` | `https://{channel}.release.flatcar-linux.net/amd64-usr/current/version.txt` |
| `rhel-coreos` | `openshift` | `4.21.0` | `rhcos` | `core` | `4.16` | (Red Hat portal) | — (manual OVA) |
| `opensuse-microos` | `opensuse` | `1.0.0` | `openSUSE-MicroOS` | `opensuse` | `tumbleweed` | `tumbleweed` | — (manual OVA) |

Field access from consumers:
- `_platform_preset.ignition.butane_template` / `.butane_variant` / `.butane_spec_version`
- `_platform_preset.ignition.default_channel` / `.metadata_url` / `.ova_url`
- `_platform_preset.username` (top-level row field, also published as
  `instance_user_name` host fact)

To override per-deployment, edit the map row via
`instance_netbox_platform_map` in inventory (consumer merges on top of
`_platform_map`). To toggle `--strict` Butane parsing without touching
the map, set the flat `ignition_butane_strict: true` knob.

Auto-import currently parses only the FCOS stream schema. For other presets,
pre-import the OVA into the content library manually (or via vCenter UI), set
`content_library_item_name` to the imported name, and set
`content_library_auto_import: false`.

## Phases (entry points)

| Entry file | When | Invocation |
|---|---|---|
| `tasks/fcos_prepare.yml` | Before deploy | once (localhost); fetches FCOS stream metadata, imports OVA into vCenter content library (when `content_library_auto_import: true`) |
| `tasks/render_ignition.yml` | Before deploy | once (localhost); Butane → Ignition JSON for every VM in `instances_to_create` |
| `tasks/inject.yml` | After deploy | per-VM loop; writes `guestinfo.ignition.config.data` + encoding |
| `tasks/poweron.yml` | After inject | per-VM loop; powers VM on |
| `tasks/cleanup.yml` | After verify | per-VM loop; clears ignition guestinfo |

Also bundled:
- `tasks/content_library_import.yml` — reusable helper used by `fcos_prepare.yml` to import an OVA URL into a content library item. Not an entry point.

## Expected inputs (from parent role context)

- `item` (per-VM entries) — VM dict from the loop
- `instances_to_create` (render/prepare) — full list for once-per-run ops
- `ignition` — config dict: `butane_template`, `butane_variant`, `butane_spec_version`, `butane_strict`, `tmp_dir`
- `ignition_channel`, `ignition_arch`, `ignition_metadata_url` — shared scalars for all ignition presets (see instance defaults); today only consumed by `fcos_prepare.yml`, but named neutrally so the same inventory works for any preset
- `instance_platform_preset` — `fedora-coreos` / `flatcar` / `rhel-coreos` / `opensuse-microos`; supplies defaults for `butane_variant` + `butane_spec_version`
- `content_library` — `name`, `template`, `template_prefix`, `auto_import`, etc.
- `vcenter`, `instance_datacenter`, `instance_folder` — standard vCenter connection vars

## Templates

- [templates/fcos.bu.j2](templates/fcos.bu.j2) — Butane template rendered per VM before Butane→Ignition conversion.

### Ignition-time SSH port fast-path

All four Butane templates render `/etc/ssh/sshd_config.d/99-ansible.conf` with
the chosen `Port` directive at first boot when `instance_ssh_port` (or per-VM
`instances[].ssh_port`) is non-22. sshd binds the new port directly on first
boot — no transitional 22→newport migration, no second restart. The post-boot
[instance/tasks/ssh_config.yml](../instance/tasks/ssh_config.yml) task still
runs (it handles SELinux port label + `ssh.socket` disable, which Butane
can't do in initramfs), but its Phase 0 probe detects the already-migrated
state and skips the transition phase. Filename matches the post-boot task's
drop-in so both code paths converge on a single file.

## Requirements

- `butane` binary on the controller for render_ignition.yml. Install via:
  - Fedora: `dnf install butane`
  - macOS: `brew install butane`
  - Docker (no install): wrap with `docker run --rm -i quay.io/coreos/butane:release`
- `community.vmware` collection on the controller for content-library import tasks.

## Role outputs

### `ignition_status` — per-VM phase tracking

```yaml
ignition_status:
  <vm_name>:
    butane_rendered: "/tmp/ignition/<vm>.bu"    # after render_ignition.yml
    ignition_file: "/tmp/ignition/<vm>.ign"    # after render_ignition.yml
    injected: true                              # after inject.yml
    powered_on: true                            # after poweron.yml
    cleaned_up: true                            # after cleanup.yml
```

### `fcos_import_facts` — what was imported (populated by `fcos_prepare.yml`)

```yaml
fcos_import_facts:
  stream: stable
  arch: x86_64
  version: "40.20240322.3.1"
  ova_url: "https://builds.coreos.fedoraproject.org/prod/streams/stable/builds/..."
  library_name: "fcos-library"
  library_item_name: "fedora-coreos-40.20240322.3.1"
```

Useful for downstream roles that need to reference the imported OVA (e.g. to
deploy VMs from it without re-fetching metadata).

## Gotchas

- **FCOS metadata endpoint** — defaults to `https://builds.coreos.fedoraproject.org/streams/<stream>.json`. Set `ignition_metadata_url` to override (air-gapped / mirror).
- **Auto-import lag** — pulling the OVA into vCenter's content library from `builds.coreos.fedoraproject.org` can take 5+ minutes over slow WAN links; `fcos_prepare.yml` waits synchronously.
- **Butane binary required** — no pure-Python Butane implementation exists; `butane` must be in PATH or replaced with a container wrapper.
