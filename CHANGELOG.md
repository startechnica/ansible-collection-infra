# Changelog

All notable changes to this collection are documented in this file. The format
is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this collection adheres to [Semantic Versioning](https://semver.org/).

## 1.0.1 (2026-05-25)

### Fixed
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
- `roles/instance/vars/main.yml`: canonical OS identity table
  `_platform_map`, keyed by NetBox platform slug and linking
  `ansible_facts.os_family`, `ansible_facts.distribution`, and the
  vSphere `guestId` for each supported guest. The existing
  `_vsphere_guest_id_platform_map` is now derived from it so there's a
  single source of truth. Covers Debian/Ubuntu (incl. netbox-community
  `debian-gnulinux-N-64-bit` and `ubuntu-linux-64-bit` slug aliases),
  RHEL family (RHEL, Rocky, AlmaLinux, Oracle Linux, CentOS, Fedora,
  Fedora CoreOS), Suse (SLES, openSUSE Tumbleweed), Windows desktop /
  Server 2016–2022, FreeBSD (pfSense), and MikroTik RouterOS. Aliases
  ordered before canonical slugs so the inverse map resolves to the
  canonical entry per `guestId`.

### Lint cleanup
- ~60 missing task names added across mongodb / patroni entry-point
  playbooks and the `instance` role's molecule converge tests.
- Handler / task name casing normalized.
- 7× intentional shell probes (curl mTLS, `systemctl is-active`,
  `rpm -q`) marked `# noqa: command-instead-of-module` with inline
  rationale.
- Long YAML loop tables (TLS cert specs, service maps) had
  column-alignment padding stripped to satisfy yamllint defaults.

## 1.0.0 (2026-05-25)
- Initial release