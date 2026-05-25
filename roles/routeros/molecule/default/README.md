# Molecule scenario — routeros

**Status:** deferred — needs live RouterOS or CHR instance.

A functional molecule test of this role requires a reachable MikroTik API
endpoint. Two practical paths:

- **RouterOS CHR in a VM** — MikroTik publishes a free Cloud Hosted Router
  image; boot it in QEMU/VirtualBox/vCenter, set an admin password, expose
  the API port (8728/8729) to the molecule host, and point `routeros_host`
  at it in `converge.yml`.
- **Network lab with a physical MikroTik** — set `routeros_host` to your
  lab device; not appropriate for shared CI but useful for integration
  validation from a workstation.

`librouteros` stub libraries don't exist as standalone mockable services,
so syntax-only coverage is all that's feasible in this shared repo.

Interim coverage via `ansible-playbook --syntax-check` + `ansible-lint`
in CI.
