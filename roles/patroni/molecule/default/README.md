# Molecule scenario — patroni

**Status:** deferred — needs multi-node Docker-in-Docker scaffold.

A meaningful test of the `patroni` role requires:

- **3 target containers** (`patroni_nodes` group) so etcd can reach quorum and
  Patroni can actually elect a leader.
- **Docker inside each container** (docker-in-docker, privileged) for the
  Patroni / etcd / HAProxy / PgBouncer / vip-manager containers the role
  deploys.
- **A shared L3 network** — nodes must reach each other on etcd client/peer
  ports, Patroni REST API, Postgres streaming replication, and HAProxy
  backends.
- **Capability to bind a VIP** — vip-manager / keepalived both need
  `CAP_NET_ADMIN` + `CAP_NET_RAW`, which are already provided by the
  `privileged: true` platform default but must be verified per-container.

This is a larger scaffold than the single-node reference in
[roles/preflight/molecule/default/](../../../preflight/molecule/default/) and
is left as follow-up work.

**Interim coverage** via `ansible-playbook --syntax-check` and `ansible-lint`
in CI.

## Scaffold outline (for when someone picks this up)

```yaml
# molecule.yml (excerpt)
platforms:
  - name: patroni-01
    groups: [patroni_nodes]
  - name: patroni-02
    groups: [patroni_nodes]
  - name: patroni-03
    groups: [patroni_nodes]
provisioner:
  inventory:
    group_vars:
      patroni_nodes:
        patroni_scope: molecule-pg
        postgresql_postgres_password: moleculetest
        patroni_vip_address: ""               # disable VIP since single-host molecule
        vip_manager: "none"
```

`prepare.yml` installs Docker + cryptography inside each container, and
`converge.yml` imports the `patroni` role on the group.
