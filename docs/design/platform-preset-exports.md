# Design: platform-preset values via `common` exports

Status: **in progress**. Phase 0, the `grafana_alloy` part of Phase 1 and the
`instance_ignition` / `instance_cloud_init` part of Phase 4 have landed;
everything else is planned.
Target: 1.0.3. Phases 1–4 change nothing in `deploy.yml` runs; Phase 5 is breaking.
Date: 2026-09-14 (updated 2026-09-15)

## Why

`roles/common/tasks/resolve_platform_preset.yml` resolves six OS knobs against
`_platform_map` and uses `set_fact` to write the results back over the inventory names:

| Inventory input (bare name) | Resolved value (common `vars/main.yml`) |
|---|---|
| `container_engine` | `_resolved_container_engine` |
| `instance_init_type` | `_resolved_instance_init_type` |
| `instance_user_name` | `_resolved_instance_user_name` |
| `content_library_name` | `_resolved_content_library_name` |
| `content_library_item_name` | `_resolved_content_library_item_name` |
| `ignition_channel` | `_resolved_ignition_channel` |

It also publishes `_platform_map`, `_platform_preset` and
`_content_library_item_pinned` as host facts. Every other role reads the bare
names and relies on the resolver having run earlier on the same host. That
causes four problems:

- **Hidden ordering.** `deploy.yml` runs the resolver in Stages 0, 2 and 3.
  Standalone playbooks often never run it, so they read raw inventory values:
  - `playbooks/{mongodb,patroni}/_setup.yml:21` set `ansible_user` from
    `instance_user_name | default('ubuntu')`. An FCOS inventory that relies on
    the preset gets `ubuntu` instead of `core` when provisioning didn't run in
    the same invocation, for example `deploy_mongodb.yml --tags mongodb` or
    `patroni/install.yml`.
  - `grafana_alloy`, `mongodb` and `patroni` default their engine to
    `container_engine | default('podman')`. With an Ubuntu preset and
    `container_engine` unset, a run outside `deploy.yml` gets podman.
- **Inconsistent fallbacks.** `preflight`, `validate_inventory`,
  `build_host_groups`, `fcos.bu.j2` and the patroni handlers fall back to
  `docker` for an unset `container_engine`. The service roles fall back to
  `podman`, and so does the resolver itself.
- **The input is overwritten.** The resolver replaces
  `content_library_item_name` with the preset's value. So it has to record
  "did the user pin an OVA?" in `_content_library_item_pinned` first, before
  that information is lost.
- **Team rule.** Roles share vars by importing the producer role's exports, not
  through host facts. `mongodb_smoke` and `patroni_smoke` already work this way.

## Target state

- The bare names stay as **inputs** in inventory, role defaults,
  argument_specs, examples and test fixtures. Nothing is renamed.
- Every **reader** uses the `_resolved_*` name, loaded with
  `import_role: {name: common, tasks_from: export_vars}`, directly or through
  another role's `export_vars` that imports it, such as `instance`'s.
- Every role works on its own, including next to roles from other
  collections: each entry point imports the exports it reads (rule 6).
- `resolve_platform_preset.yml` and `_content_library_item_pinned` are deleted.
  No host fact carries a preset value.

## Rules for consumers

1. **Import once, statically, where the role's tasks start.** That is
   `tasks/main.yml`, every `tasks_from` entry file of a role that has no
   `main.yml` entry point, or the role's own `export_vars.yml` when another
   role imports it. The vars cover the whole play: argument validation,
   handlers, nested `include_role`s, and play-level tasks after the include all
   see them. They don't carry into the next play, so import again in each play
   that reads them.
2. **A role's own selector defaults to the resolved name**, for example
   `<role>_container_engine: "{{ _resolved_container_engine }}"`. Tasks,
   templates and handlers read the role var, not `container_engine`.
3. **Drop the `| default(...)` on reads.** `_resolved_*` always has a value.
4. **Task files inside `common` need no import.** `common/vars/main.yml` is
   already in scope, so they read `_resolved_*` directly.
5. **Pass resolved values to `add_host` hosts.** Hosts created with `add_host`
   get resolved values as their host vars. For a dynamic host that is its
   inventory, not a fact, so `_resolved_*` on that host returns the same value.
6. **Every entry point imports what it reads.** A role must work on its own,
   next to roles from other collections, so it can't count on a parent role or
   on host facts. `instance_ignition` and `instance_cloud_init` import
   `instance` `export_vars` in each phase file that reads its vars, even though
   `instance` already provides them when it includes those files. Imports stay
   visible until the end of the play, so a role that reads the same names with
   fallbacks of its own belongs in a separate play: `deploy.yml` Stage 2.1 runs
   `netbox_register` apart from `instance` for that reason.

Verified on ansible-core 2.19.5 (2026-09-13/14): all of rule 1, including a
`roles:` entry, a dynamic `include_role`, an import inside an `include_tasks`
file, a chained `export_vars` → `export_vars` import, and `include_role`
without `public: true` not exposing the vars. Verified 2026-09-15 for rule 6:
an import nested in a file reached by `include_role`, even one included from
inside the parent role, stays visible to the rest of the play.

## Why the phase order is safe

Until Phase 5 the resolver keeps running everywhere it runs today. Right after
it runs, `_resolved_X` equals `X`. Switching a reader from `X` to `_resolved_X`
therefore changes nothing in `deploy.yml`. Behaviour only changes where the
resolver never ran, which is exactly the bugs listed above.

**Exception:** the auto-import gate at `roles/instance/tasks/main.yml:134`
must switch in the same change that deletes the resolver (see Phase 5).

## Phase 0 — export anchor (done)

`roles/common/tasks/export_vars.yml`.

## Phase 1 — service roles

| File | Line(s) | Today | Change |
|---|---|---|---|
| `roles/grafana_alloy/defaults/main.yml` | 22 | `container_engine \| default('podman')` | `_resolved_container_engine` — **done** |
| `roles/grafana_alloy/tasks/main.yml` | top | — | import common `export_vars` — **done** |
| `roles/grafana_alloy/meta/argument_specs.yml` | 26–28 | documented default | match defaults — **done** |
| `roles/mongodb/defaults/main/engine.yml` | 9 | `container_engine \| default('podman')` | `_resolved_container_engine` |
| `roles/mongodb/tasks/main.yml` | top | — | import common `export_vars` |
| `roles/mongodb/tasks/export_vars.yml` | — | task-free | import common `export_vars`, so `mongodb_smoke` (reads `mongodb_container_engine`) still resolves |
| `roles/patroni/defaults/main/patroni.yml` | 36 | `container_engine \| default('podman')` | `_resolved_container_engine` |
| `roles/patroni/tasks/main.yml` | top | — | import common `export_vars` |
| `roles/patroni/tasks/export_vars.yml` | — | task-free | import common `export_vars` (for `patroni_smoke`) |
| `roles/patroni/handlers/main.yml` | 114, 121 | `container_engine \| default('docker')` | `patroni_container_engine`. Today these ignore a per-role override and fall back to `docker` |
| `roles/preflight/tasks/main.yml` | top; 28; 67, 72 | —; `instance_init_type \| default('ignition')`; `container_engine \| default('docker')` | import; `_resolved_instance_init_type`; `_resolved_container_engine` |
| `roles/preflight/tasks/venv.yml` | 25, 38, 45 | `instance_init_type \| default('ignition')` | `_resolved_instance_init_type` |

`preflight`'s other entry points (`docker_install_fcos`, `docker_login`,
`podman_login`) don't read these names.

## Phase 2 — playbooks

| File | Line(s) | Today | Change |
|---|---|---|---|
| `playbooks/mongodb/_setup.yml` | top; 21 | —; `instance_user_name \| default('ubuntu')` | import; `_resolved_instance_user_name` |
| `playbooks/patroni/_setup.yml` | top; 21 | same | same |
| `playbooks/deploy.yml` Stage 3.4 | 166, 177 | `instance_init_type \| default('ignition')` | `_resolved_instance_init_type`; import as the play's first task |
| `playbooks/deploy.yml` Stage 3.7 | 258 | `instance_{{ instance_init_type }}` (localhost) | `_resolved_instance_init_type`; import in the play. **Required before Phase 5:** after it, localhost's bare value is the raw inventory value, which may be empty |
| `playbooks/deploy.yml` Stage 4 | 286; 304, 308 | `container_engine \| default('docker')`; `instance_init_type \| default('ignition')` | `_resolved_*`; import first in `pre_tasks` |

The `_setup.yml` files are loaded with `include_tasks`. A static import inside
them makes the vars visible to the `add_host` loop that follows.

## Phase 3 — `common` task files (no import needed)

| File | Line(s) | Today | Change |
|---|---|---|---|
| `roles/common/tasks/validate_inventory.yml` | 18, 19, 58, 59, 101, 102, 107, 373, 377, 387 | bare names, `default('docker')` / `default('ignition')` | `_resolved_*`. Leave the prose at 29, 31, 75, 78, 80, 81, 98, 104 as it is |
| `roles/common/tasks/build_host_groups.yml` | 33, 79 | `ansible_user: instance_user_name \| default('ubuntu')` | `_resolved_instance_user_name` |
| | 45, 87 | `instance_init_type \| default('ignition')` | `_resolved_instance_init_type` |
| | 52, 88 | `container_engine \| default('docker')` | `_resolved_container_engine` |
| `roles/common/tasks/wait_for_cloud_init.yml` | 45 | `instance_init_type \| default('ignition')` | `_resolved_instance_init_type` |

The `include_tasks: resolve_platform_preset.yml` lines stay until Phase 5.

## Phase 4 — `instance` family (Stage 2, localhost)

`instance_ignition` and `instance_cloud_init` are done. Each phase that reads
`instance` or preset vars imports `roles/instance/tasks/export_vars.yml`,
which imports common's, so the phase runs without `instance` (rule 6).
`resolve_butane.yml` and `poweron_mark.yml` read neither and don't import. The
`set_fact` that copied `instance_folder_path` and `ignition_tmp_dir` to
localhost for Stage 3.7 is gone. `content_library_import.yml:60` still sets
the bare `content_library_item_name`; `_resolved_content_library_item_name`
prefers a non-empty bare value, so readers after the import get the imported
name.

`deploy.yml` runs `netbox_register` in its own play, Stage 2.1. The imports
keep the `instance` defaults visible until the end of the Stage 2 play, and
`roles/netbox_register/tasks/register_vm.yml` falls back to
`instance_platform_preset`, `instance_cpu`, `instance_memory_mb`,
`instance_disks` and `portgroup_name`. In the same play, NetBox would get
`fedora-coreos`, 2 vCPUs, 2048 MB and `VM Network` for VMs that don't set
those values.

Still to do: add `import_role: {name: common, tasks_from: export_vars}` to
`roles/instance/tasks/main.yml`, next to the resolver include at lines 9–12,
and switch the `instance` readers.

| File | Line(s) | Today | Change |
|---|---|---|---|
| `roles/instance/tasks/main.yml` | 81, 88, 103, 132, 154 | `instance_init_type` | `_resolved_instance_init_type` (134 waits for Phase 5) |
| `roles/instance/tasks/prepare_all.yml` | 44, 66 | role-name dispatch on `instance_init_type` | `_resolved_instance_init_type` |
| `roles/instance/tasks/configure_all.yml` | 37 | same | same |
| `roles/instance/tasks/ssh_config.yml` | 74, 139, 198, 226, 256, 327, 349, 372 | `instance_user_name` | `_resolved_instance_user_name` |
| `roles/instance/tasks/validate/template.yml` | 8–11, 15–16, 22–23, 26, 32–33, 39, 44–45, 54, 87–88 | `content_library_name`, `content_library_item_name` | `_resolved_content_library_name`, `_resolved_content_library_item_name` (prose at 13–14, 17 can keep the input names) |
| `roles/instance_ignition/tasks/{fcos_prepare,content_library_import_attempt,post_boot_extras}.yml`, `roles/instance_ignition/templates/{fcos,flatcar,rhcos,opensuse}.bu.j2`, `roles/instance_cloud_init/tasks/post_provision.yml`, `roles/instance_cloud_init/templates/cloudinit-user-data.yml.j2` | — | bare `ignition_channel`, `content_library_name`, `instance_user_name`, `container_engine` | `_resolved_*` — **done** |
| `tests/render-templates.yml` | — | cloud-init play doesn't load common vars | loads `../roles/common/vars/main.yml` — **done** |
| `playbooks/deploy.yml` Stage 2 | — | `netbox_register` in the provisioning play | its own play, Stage 2.1 — **done** |

## Phase 5 — remove the resolver (breaking)

| File | Change |
|---|---|
| `roles/common/tasks/resolve_platform_preset.yml` | Delete |
| `roles/common/tasks/validate_inventory.yml` | Remove the include at 9–10 (and its comment at 6–8) |
| `roles/common/tasks/build_host_groups.yml` | Remove the include at 23–24 (and its comment at 20–22) |
| `roles/instance/tasks/main.yml` | Remove the include at 9–12 (the Phase 4 import stays) and rewrite the header comment at 1–8 |
| `roles/instance/tasks/main.yml` | Change line 134 from `not (_content_library_item_pinned \| default(false) \| bool)` to `content_library_item_name \| default('') \| length == 0`, and rewrite the comment at 111–126 |
| `roles/common/tasks/main.yml`, `roles/common/README.md`, `roles/common/tasks/export_vars.yml` | Remove the `resolve_platform_preset` mentions |
| Comments: `roles/instance/defaults/main.yml` (102–111, 118–129, 250–254, 311–317, 329–333, 508–514, 527–532), `roles/instance/meta/argument_specs.yml` (112–168), `roles/common/defaults/main.yml` (2–14) | Replace "resolved at role entry by `resolve_platform_preset.yml`" with "falls back through common's `_resolved_*`" |
| `CHANGELOG.md` (Breaking changes), `docs/UPGRADING.md` | Document the break (below) |

**Why the gate at line 134 moves in this phase and not earlier.** While the
resolver still runs, the bare `content_library_item_name` already holds the
preset value (for example `fedora-coreos`). `length == 0` would never be true,
and auto-import would silently stop. That is the bug
`_content_library_item_pinned` was added to fix. Once the resolver is gone, the
bare name is the raw inventory value again, and "empty means not pinned" works
without a snapshot.

**What breaks.** Anything outside the collection that uses
`tasks_from: resolve_platform_preset`, or that expects a resolved value in
`container_engine`, `instance_init_type`, `instance_user_name`,
`content_library_*`, `ignition_channel`, `_platform_preset` or `_platform_map`.
Migration: import common `export_vars` and read `_resolved_*`. There is no
compatibility alias. As of 2026-09-14, the `skydata-sites` playbooks do neither;
its inventories only set the inputs.

**Watch `inherit_localhost_vars`.** After this phase it copies localhost's raw
inputs to the targets, not resolved values. `_resolved_*` still gives the same
answer there, because `instance_platform_preset` is copied along with them.
`playbooks/destroy.yml` runs `validate_inventory` before
`instance tasks_from: destroy`, but `destroy` reads none of these names.

## Verification per phase

- `yamllint` on the changed files, and `ansible-playbook --syntax-check` on
  `playbooks/deploy*.yml`, `playbooks/mongodb/*.yml` and `playbooks/patroni/*.yml`.
- A scratch inventory with a local connection, run through each changed consumer.
  Three cases:
  - An Ubuntu-preset host with `container_engine` unset resolves `docker`.
  - A host with an explicit value keeps it.
  - A host that can't see the preset gets the `fedora-coreos` row.
- Phase 4: `ansible-playbook tests/render-templates.yml`, and each sub-role
  phase included from a play without `instance` or the resolver.
- Phase 5: a real `deploy.yml` run against one FCOS and one Ubuntu test
  inventory. Include an unpinned `content_library_item_name` with
  `content_library_auto_import: true` (auto-import must fire) and a pinned one
  (it must not).

## Follow-ups (outside this plan)

- `playbooks/{mongodb,patroni}/_setup.yml` duplicate `build_host_groups`'
  `add_host` call, but don't pass `instance_init_type` or `container_engine`.
  Calling `common` `build_host_groups` instead would remove the duplicate.
- `preflight` checks the host-wide engine even when a role selects its own.
  `mongodb`, `patroni` and `grafana_alloy` include it without an engine var. A
  `preflight_container_engine` var, passed by each caller, would fix that.
- `content_library_import.yml:60` still returns the imported OVA name by
  `set_fact` on the bare input name.
- `instance_ignition/tasks/inject.yml:18`,
  `instance_cloud_init/tasks/{inject,poweron,post_provision}.yml` (23, 21, 18)
  and `netbox_register/tasks/register_vm.yml:47, 298, 402` build
  `/{{ instance_datacenter }}/vm/{{ instance_folder }}` by hand instead of
  reading `instance_folder_path`. With an empty `instance_folder` that gives
  `/<dc>/vm/`, the trailing-slash form `instance/vars/main.yml` avoids.
- Each per-VM phase include loads `instance` and `common` again. A synthetic
  run on 2026-09-15 (40 includes, then 300 tasks in the same play) took 11.9 s
  with the import and 5.6 s without: about 0.1 s per include, plus a small cost
  per later task for each loaded copy. If large fleets matter, load once per
  play with a guarded `include_role: {name: instance, tasks_from: export_vars, public: true}`.
- The `instance` defaults include unprefixed names (`debug`, `instances`,
  `container_engine`, `docker_enabled`, `podman_enabled`, `portgroup_name`).
  The imports make them visible to every later role in the play, including
  roles from other collections.
- `netbox_register` reads `instance` inputs (`instance_cpu`,
  `instance_memory_mb`, `instance_disks`, `portgroup_name`,
  `instance_platform_preset`, …) with fallbacks of its own instead of importing
  `instance` `export_vars`. Run on its own, it doesn't see the `instance`
  defaults.
