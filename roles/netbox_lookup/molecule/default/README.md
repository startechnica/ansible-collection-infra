# Molecule scenario — netbox_lookup

**Status:** syntax-only scaffold.

The role queries a live NetBox API, so a functional molecule scenario needs a
stub HTTP server responding to:

- `GET /api/virtualization/clusters/?name=<n>` → returns `{count, results:[{id, name}]}`
- `GET /api/virtualization/virtual-machines/?cluster_id=<id>` → VM list
- `GET /api/ipam/vrfs/?name=<n>` → VRF lookup
- `GET /api/ipam/ip-addresses/?virtual_machine=<name>&vrf_id=<id>` → IP per VM
- `GET /api/extras/tags/?id__in=<csv>` → tag slugs

Minimal path: a `prepare.yml` that installs `python3-aiohttp` and starts a tiny
stub on `127.0.0.1:8081`, then set `netbox_connect.url` to it in `converge.yml`.

Until that's wired, this scaffold uses `import_role` + `when: false` to at least
catch syntax / argument_specs / defaults issues during `molecule converge`.

## Run

```bash
cd roles/netbox_lookup
molecule test
```
