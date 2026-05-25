# Molecule scenario — netbox_register

**Status:** syntax-only scaffold.

Upgrading to functional: add a `prepare.yml` that boots a stub accepting:

- `GET /api/virtualization/clusters/?name=<n>` / `…/virtual-machines/`
- `POST /api/virtualization/virtual-machines/` + `PATCH /<id>/`
- `GET /api/ipam/vrfs/?name=<n>`
- `POST /api/ipam/ip-addresses/`
- `POST /api/ipam/services/`
- `GET /api/extras/custom-fields/` + `POST /api/extras/custom-fields/`

The ensure_custom_fields + register_vm tasks should both get exercised, and
`verify.yml` can assert the stub received the expected payloads.

## Run

```bash
cd roles/netbox_register
molecule test
```
