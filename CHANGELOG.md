# Changelog

All notable changes to this collection are documented in this file. The format
is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this collection adheres to [Semantic Versioning](https://semver.org/).

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

### Added
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
  `content_library_template` (defaults to the slug). The 4 ignition
  rows (`fedora-coreos`, `flatcar`, `rhel-coreos`, `opensuse-microos`)
  additionally carry an `ignition:` sub-dict with butane_variant /
  butane_spec_version / butane_template / template_prefix /
  default_channel / channels / metadata_url / ova_url — the data that
  used to live in `ignition_flavour_map`.
- `instance_platform_preset` — new top-level knob that picks an OS row
  from `_platform_map` to supply slug-based defaults for
  `content_library_name`, `content_library_template`, `instance_user_name`,
  `instance_init_type`, and `container_engine`. Explicit inventory
  overrides on those five vars still win; unset (empty) falls back to
  the slug-derived value, then to a hardcoded final fallback
  (`ubuntu` / `ignition` / `podman` / `""`).
- `_resolved_content_library_name`, `_resolved_content_library_template`,
  `_resolved_instance_user_name`, `_resolved_instance_init_type`,
  `_resolved_container_engine` — derived vars in
  `roles/common/vars/main.yml` that implement the slug-fallback
  precedence chain (inventory non-empty > `_platform_map[<preset>].<field>`
  > hardcoded final fallback). Consumers across `common`, `instance`,
  `instance_cloud_init`, `instance_ignition`, `mongodb`, `patroni`,
  and `preflight` now read these `_resolved_*` names so the slug-based
  preset reaches every dispatch site (ansible_user on dynamic hosts,
  container runtime selection, Butane templates, etc.).

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