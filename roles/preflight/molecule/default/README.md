# Molecule scenario — preflight

Reference pattern for adding molecule tests to roles in this collection.

## Run locally

```bash
cd roles/preflight
pip install 'molecule>=24' 'molecule-plugins[docker]'
molecule test
```

Individual steps:

```bash
molecule create        # spin up the Ubuntu 24.04 container
molecule converge      # run the role
molecule verify        # run verify.yml assertions
molecule destroy       # tear down
molecule login         # shell into the container
```

## What this scenario covers

- Role runs cleanly to completion
- Docker + compose detection works
- cryptography library check passes (molecule host has it)

## Extending to other roles

**Leaf roles** (netbox_lookup, netbox_register, vcenter_sync): copy this scenario
directory, adjust `converge.yml` for the role under test, add asserts to
`verify.yml`. Mock NetBox/vCenter with a stubbed HTTP endpoint in `prepare.yml`
if needed.

**Cluster roles** (mongodb, patroni): need a 3-node setup with working Docker
inside each container (docker-in-docker with `--privileged`). That's a bigger
scaffold — not attempted in this starter scenario.
