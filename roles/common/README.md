# common

Utility tasks consumed by every other role in this collection and by the
top-level playbooks. Not a role in the "invoke once and it does things"
sense — it's a **bag of named task files** you pick from via `tasks_from:`.

```yaml
- ansible.builtin.include_role:
    name: common
    tasks_from: <wait_for_ssh | inherit_localhost_vars | build_host_groups | validate_inventory>
  vars:
    # task-specific args here
```

## Tasks

| `tasks_from:` | Runs on | Purpose |
|---|---|---|
| `wait_for_ssh` | `hosts: localhost` | Port-probe + known_hosts refresh for a group of hosts. Uses `ssh-keygen -R` + `ssh-keyscan` so freshly-recreated VMs (same IP, new host key) don't trigger "REMOTE HOST IDENTIFICATION HAS CHANGED". |
| `inherit_localhost_vars` | any play context | Copies non-magic vars from `hostvars['localhost']` onto the current host via a temp-file + `include_vars` trick. Used as a pre_task to surface controller-side facts (artifacts_dir, instance_tags, etc.) inside service roles. |
| `build_host_groups` | `hosts: localhost` | Consumes the `instances` list and dispatches entries into `cluster_nodes` + (optionally) `mongodb_nodes` / `patroni_nodes` groups via `add_host`. Connection params (user, key, port) are stitched in from `default_user` and `instance_ssh_configs`. |
| `validate_inventory` | `hosts: localhost` | Fail-fast assertions — catches S3 all-or-nothing, missing `patroni_scope`, `vip_manager` without `patroni_vip_address`, malformed `databases[]`, etc. Invoked as Stage 0 of `deploy.yml` via the `always` tag. |

Each one has a detailed comment header inside its task file and is
documented with `argument_specs` in [meta/argument_specs.yml](meta/argument_specs.yml).

## Parameters by task

Full arg specs live in [meta/argument_specs.yml](meta/argument_specs.yml).
Quick reference:

### `wait_for_ssh`

```yaml
- include_role:
    name: common
    tasks_from: wait_for_ssh
  vars:
    ssh_wait_group: cluster_nodes       # which group to probe (default)
    ssh_wait_timeout: 60                # seconds per host
```

### `inherit_localhost_vars`

Takes no parameters. Uses `/tmp/.ansible_localhost_vars.yml` as a scratch file.

### `build_host_groups`

```yaml
- include_role:
    name: common
    tasks_from: build_host_groups
  vars:
    target_groups: [cluster_nodes]      # groups every VM joins (default)
    use_tags: false                     # true = route by instances[].tags too
```

When `use_tags: true`:
- All VMs → `cluster_nodes`
- VMs with `mongodb` tag → also `mongodb_nodes`
- VMs with `patroni` tag → also `patroni_nodes`

### `validate_inventory`

Takes no parameters. Reads inventory vars and asserts.

## Design notes

- **Localhost-first:** every task except `inherit_localhost_vars` expects to
  run in a `hosts: localhost` context. They're orchestration primitives,
  not target-side tasks.
- **No side-effects on skip:** each task file is safe to `include_role`
  speculatively — if the invariant it enforces is already met, it no-ops.
- **Short names internally:** within this collection, references use
  `name: common` (not the FQCN `startechnica.infra.common`) so source-tree
  development works without `ansible-galaxy collection install`.

## Adding a new utility task

1. Write `tasks/<name>.yml` — a list of tasks, no play wrapper.
2. Document the inputs in `meta/argument_specs.yml` under a new
   top-level key matching the filename.
3. Update `tasks/main.yml`'s fail message to list the new entry point.
4. Reference it from callers via `include_role: { name: common, tasks_from: <name> }`.

## See also

- [playbooks/deploy.yml](../../playbooks/deploy.yml) — top-level caller.
- [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) — where `common` sits in
  the role topology.
