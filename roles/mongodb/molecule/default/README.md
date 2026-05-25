# Molecule scenario — mongodb

**Status:** deferred — needs multi-node Docker-in-Docker scaffold.

A meaningful test of the `mongodb` role requires:

- **3 target containers** (Ansible host group `mongodb_nodes`) so replica-set
  initialization and `rs.add()` can actually produce a 3-member RS.
- **Docker inside each container** (docker-in-docker with `--privileged` and
  cgroupns_mode: host) so `community.docker.docker_compose_v2` has a daemon to
  talk to.
- **Shared L3 network** between the three containers so mongod/mongos/configsvr
  replica set members can reach each other over the TLS-protected ports.
- **Valid TLS material** — the role generates certs from defaults, so this
  works with the molecule platform CA as long as file paths line up.

This is a larger scaffold than the single-node reference in
[roles/preflight/molecule/default/](../../../preflight/molecule/default/) and
is left as follow-up work.

**Interim coverage** happens through:

- `ansible-playbook --syntax-check playbooks/mongodb/*.yml` in CI (catches
  YAML / argument_specs / template-rendering errors).
- `ansible-lint roles/mongodb/` (catches deprecated patterns, missing
  `changed_when`, missing handler names, etc.).

## Scaffold outline (for when someone picks this up)

```yaml
# molecule.yml (excerpt)
platforms:
  - name: mongodb-01
    ...
    groups: [mongodb_nodes]
  - name: mongodb-02
    groups: [mongodb_nodes]
  - name: mongodb-03
    groups: [mongodb_nodes]
provisioner:
  inventory:
    group_vars:
      mongodb_nodes:
        mongodb_cluster_type: replicaset
        mongodb_admin_password: moleculetest
```

Then a `prepare.yml` that installs Docker inside each container via the same
`geerlingguy.docker` role used in production, and `converge.yml` simply imports
`mongodb` on the group.
