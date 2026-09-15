# instance_ignition

Ignition phases for [instance](../instance/). Supports Fedora CoreOS,
Flatcar Container Linux, Red Hat CoreOS (RHCOS), and openSUSE MicroOS —
pick via `instance_platform_preset` (see *Preset support* below).

Has no `main.yml` entry point: include one phase at a time with
`include_role: tasks_from:`. `instance` does that, but any playbook can. Each
phase imports `instance` `export_vars`, so the `instance` defaults and the
platform preset resolve without `instance` running first.

## Preset support

`instance_platform_preset` (an `instance` input) selects a row in
`_platform_map` (`roles/common/vars/main.yml`). The row's `ignition:`
sub-dict supplies every flavour-varying knob; callers read it as
`_platform_preset.ignition.<field>`, loaded by the `export_vars` import.

| Preset | Butane variant | Spec version | Template prefix | Default user | Default channel | Channels | Metadata URL |
|---|---|---|---|---|---|---|---|
| `fedora-coreos` | `fcos` | `1.5.0` | `fedora-coreos` | `core` | `stable` | `stable`, `testing`, `next` | `https://builds.coreos.fedoraproject.org/streams/{channel}.json` |
| `flatcar` | `flatcar` | `1.1.0` | `flatcar-production-vmware` | `core` | `stable` | `stable`, `beta`, `alpha`, `lts` | `https://{channel}.release.flatcar-linux.net/amd64-usr/current/version.txt` |
| `rhel-coreos` | `openshift` | `4.21.0` | `rhcos` | `core` | `4.16` | (Red Hat portal) | — (manual OVA) |
| `opensuse-microos` | `opensuse` | `1.0.0` | `openSUSE-MicroOS` | `opensuse` | `tumbleweed` | `tumbleweed` | — (manual OVA) |

Field access from consumers:
- `_platform_preset.ignition.butane_template` / `.butane_variant` / `.butane_spec_version`
- `_platform_preset.ignition.default_channel` / `.metadata_url` / `.ova_url`
- `_platform_preset.username` (top-level row field; phases read the user name
  as `_resolved_instance_user_name`, which prefers `instance_user_name`)

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
| `tasks/resolve_butane.yml` | Before `fcos_prepare.yml` | once (localhost); publishes `_butane_bin` — `butane` from `PATH`, else the pinned release downloaded into `ignition_butane_cache_dir`; fails when neither works (`butane_resolve_dry_run: true` only probes and warns; `deploy.yml` Stage 0 uses that) |
| `tasks/fcos_prepare.yml` | Before deploy | once (localhost); fetches FCOS stream metadata, imports OVA into vCenter content library (when `content_library_auto_import: true`) |
| `tasks/render_ignition.yml` | Before deploy | once (localhost); Butane → Ignition JSON for every VM in `instances_to_create` |
| `tasks/inject.yml` | After deploy | per-VM loop; writes `guestinfo.ignition.config.data` + encoding |
| `tasks/poweron.yml` | After inject | per-VM loop; powers VM on |
| `tasks/cleanup.yml` | After `bootstrap_phase=ready` | per-VM loop (`deploy.yml` Stage 3.7); clears ignition guestinfo and temp files |

Also bundled:
- `tasks/content_library_import.yml` — reusable helper used by `fcos_prepare.yml` to import an OVA URL into a content library item. Not an entry point.

## Expected inputs

Each phase that reads these loads the `instance` defaults and vars itself
through `instance` `export_vars`, so set only what differs from those defaults.
`resolve_butane.yml` reads none of them, only this role's `ignition_butane_*`
defaults.

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

- `butane` binary on the controller for render_ignition.yml, resolved up front
  by `resolve_butane.yml`:
  1. `butane` on `PATH` is used as-is.
  2. Otherwise (`ignition_butane_download: true`, the default) the release
     pinned in `ignition_butane_version` is downloaded from
     `ignition_butane_release_url` into `ignition_butane_cache_dir`
     (default `~/.cache/startechnica/butane/<version>/`) and checked against
     `ignition_butane_checksums`. Linux x86_64 / aarch64 / ppc64le / s390x
     and macOS x86_64 / arm64 are pinned.
  3. Otherwise the run fails with install instructions.

  To bump the version, update `ignition_butane_version` and every
  `ignition_butane_checksums` entry together (sha256 digests are on the
  GitHub release page). Air-gapped controllers: install `butane` on `PATH`
  (Fedora `dnf install butane`, macOS `brew install butane`, or the static
  binary), or mirror the release assets and point `ignition_butane_release_url`
  at the mirror.
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
- **Butane binary required** — no Python Butane implementation exists, so the role shells out to the `butane` binary. It is downloaded automatically when missing from `PATH` (see [Requirements](#requirements)); set `ignition_butane_download: false` to require an operator-installed copy.
