# Molecule scenario — vcenter_sync

**Status:** syntax-only scaffold.

The role talks to both vCenter (pyvmomi / vmware.vmware) and NetBox. A
functional molecule scenario would need to stub both sides. Neither has a
lightweight emulator; the practical path is either:

- Run against a lab vCenter + NetBox from your developer workstation (out of
  CI), or
- Mock the two API surfaces in `prepare.yml` (significant project).

Until then, this scaffold ensures the role's YAML, defaults, and argument_specs
parse cleanly on every commit.

## Run

```bash
cd roles/vcenter_sync
molecule test
```
