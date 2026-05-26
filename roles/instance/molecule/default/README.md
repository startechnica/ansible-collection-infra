# Molecule scenario — instance

**Status:** runs against the role's pure-data validation surface
(`tasks/validate/input.yml`). No vCenter access required — the docker
platform is just a placeholder; converge plays run with `hosts: localhost
connection: local`.

## What it tests

`tasks/validate/input.yml` (pure-data input validation, no vCenter API
calls). One happy-path play exercises the normalize step + memory / NIC /
disk / network-input asserts; five negative plays each pass a
deliberately-broken `instances:` fixture and verify the expected `assert`
fires with the right `fail_msg` fragment via `block:` + `rescue:`.

What's NOT tested here (needs a live lab):
- `tasks/validate/{datastore,template,role,network}.yml` — vCenter API queries
- `tasks/validate/vms.yml` — `vmware_vm_info` folder lookup
- `tasks/create_vm/*` — actual VM creation
- `tasks/configure_all.yml` + sub-role dispatch — first-boot injection

For end-to-end against a live lab, override `vcenter_connect`,
`instance_datacenter`, `instance_cluster_name`, `instance_disks.datastore`,
`portgroup_name`, `content_library_*` in `molecule/default/group_vars/`
or via `-e`.

## Run

```bash
cd roles/instance
molecule test
```

Default scenario only — no second scenario yet.

## Files

| File | Purpose |
|---|---|
| `molecule.yml` | Driver (docker placeholder) + provisioner config |
| `group_vars/all.yml` | Common scaffolding (vcenter_connect, instance_disks, content_library_*, …) — provides the required vars for argument_specs validation |
| `converge.yml` | One play for the happy path + five negative-case plays |
| `verify.yml` | Idempotent re-run smoke check |
