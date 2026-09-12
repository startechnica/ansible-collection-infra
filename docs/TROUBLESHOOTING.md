# Troubleshooting

Known failure modes from deploying this collection, with the fix or at
least the right direction to look. Grep this file when a run fails —
chances are the error message is here.

## Controller-side

### `sudo: a password is required` on NetBox API call

```
failed: [host -> localhost] => {"msg": "...sudo: a password is required"}
```

**Cause:** the play has `become: true`, and a task `delegate_to: localhost`
inherits it. Ansible tries `sudo -n` on the controller, which fails.

**Fix:** add `become: false` to the task. The pattern applies to anything
delegated to localhost from a root-on-target play.

---

### `ModuleNotFoundError: No module named 'pyVmomi'` (or psycopg2, pymongo, docker, etc.)

**Cause:** Python deps not installed, or Ansible is using a different Python
than the one you `pip install`-ed into.

**Fix:**
```bash
source .venv/bin/activate
pip install -r requirements.txt
ansible --version                       # check the "python module location"
which ansible-playbook                   # must be the venv one
```

If `ansible-playbook` resolves outside the venv, activate it or call the venv
binary directly: `.venv/bin/ansible-playbook ...`.

---

### `pkg_resources` deprecation / `No module named 'pkg_resources'`

**Cause:** setuptools 81+ removed `pkg_resources`. One of the SDKs
(vmware.vapi, wal-g, ...) still imports it.

**Fix:** pin setuptools < 81 (already done in `requirements.txt`). If you
upgraded accidentally:
```bash
pip install 'setuptools>=68.0.0,<81'
```

---

### `cannot import name 'wrap_socket' from 'ssl'`

**Cause:** pyvmomi < 8.0.3 on Python 3.12+. `ssl.wrap_socket` was removed in
3.12.

**Fix:**
```bash
pip install 'pyvmomi>=8.0.3'
```

---

### `'ansible_architecture' is undefined` at arg-spec validation time

**Cause:** Ansible validates role `argument_specs` before `gather_facts`
has run in some play orderings. Defaults that reference raw facts fail.

**Fix:** upgrade to 0.2.0+ where the patroni role guards this. If you see
it on a custom default, wrap the fact reference:
```yaml
my_var: "{{ ansible_architecture | default('x86_64') }}"
```

## Target-side

### `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!`

**Cause:** you recreated a VM at the same IP; the old SSH host key in your
`~/.ssh/known_hosts` no longer matches.

**Fix:** the collection handles this automatically at the start of
`wait_for_ssh` — `ssh-keygen -R` + `ssh-keyscan` refresh. If you see it
outside the collection's own runs, run:
```bash
ssh-keygen -f ~/.ssh/known_hosts -R <ip>
ssh-keyscan -H <ip> >> ~/.ssh/known_hosts
```

---

### `error: externally-managed-environment` on `pip install`

**Cause:** PEP 668 — Ubuntu 24.04+, Debian 12+, and others refuse `pip
install` to system Python.

**Fix:** the collection avoids this entirely via the target-side venv at
`/opt/ansible-venv`. If you see this error, preflight didn't run — check
that mongodb/patroni's meta dep on `preflight` fired, and that the target
has `python3-venv` installed (apt).

---

### `Premature end of stream waiting for become success`

**Cause:** target host's sudo is misconfigured, password-protected, or
`ansible_user` isn't in sudoers.

**Fix:** ensure the user in `instance_user_name` has passwordless sudo.
cloud-init's default user-data already sets `sudo: "ALL=(ALL) NOPASSWD:ALL"`;
if you override `instance_user_sudo`, keep the NOPASSWD clause.

## NetBox

### NetBox 400: `cluster=<name> - Enter a valid integer`

**Cause:** NetBox filter quirks — `GET /virtualization/virtual-machines/?cluster=<name>` wants the numeric ID, not the display name.

**Fix:** the role resolves name → id at the top of `netbox_lookup.yml` /
`netbox_register.yml`. If you see this on a custom lookup, resolve the
cluster first:
```
GET /virtualization/clusters/?name=<name>
```
…grab the `id`, then query with `cluster_id=<id>`.

Same pattern for VRF: use `vrf_id=<int>`, not `vrf=<name>`.

---

### Only 1 VM lands in the RouterOS address-list even though 3 exist

**Cause:** `--tags routeros` without going through the rest of the pipeline
means `instances` isn't enriched yet (NetBox has no records for freshly-provisioned
VMs) and `cluster_nodes` group isn't built.

**Fix:** 0.2.0+ tags Stage 1 (netbox) and Stage 3 (build_host_groups) with
`routeros` too, so the prerequisites run. If you're on an older version,
run the full deploy without `--tags` once to populate NetBox, then
`--tags routeros` works standalone afterward.

---

### Services not registered in NetBox when running `playbooks/mongodb/install.yml`

**Cause:** (pre-0.2.0) Service registration used to live in `deploy.yml`
Stage 7 only; standalone `install.yml` skipped it.

**Fix:** upgrade to 0.2.0. Registration is now inside `mongodb`
(`init_cluster.yml`) and `patroni` (`start.yml`) — every install path
registers ports automatically.

## MongoDB

### Replica set unhealthy after `add-node.yml`

**Cause:** oplog window too narrow; secondary can't catch up to primary
before falling off the oplog.

**Fix:**
```bash
docker exec mongodb-mongod mongosh --port 27017 --quiet \
  --tls --tlsCertificateKeyFile /etc/mongo/ssl/healthcheck.pem \
  --tlsCAFile /etc/mongo/ssl/ca.pem \
  --authenticationMechanism MONGODB-X509 --authenticationDatabase '$external' \
  --eval "rs.printSecondaryReplicationInfo()"
```
If oplog lag > oplog size, resync from scratch:
```
docker exec mongodb-mongod mongosh ... --eval "rs.remove('<host>:27017')"
# let member reinitialize; if needed, stop it, wipe dbpath, restart
```
Then `add-node.yml` again.

## Patroni

### `FATAL: could not access file "pgaudit": No such file or directory` at bootstrap

**Cause:** `patroni_extensions` lists an extension whose shared library isn't
bundled in the Patroni image. `pg_stat_statements` is always present (Postgres
core), but `pgaudit`, `postgis`, `timescaledb`, etc. require a specially-built
image. Postgres fails `shared_preload_libraries`, Patroni renames pgdata to
`pg_data.failed`, and exits with `PatroniFatalException`.

**Fix options:**
- Remove the missing extension from `patroni_extensions`.
- Switch `patroni_image` to one that bundles the extension (Zalando's Spilo
  family bundles pgaudit + many others).
- Build a custom image: `FROM <patroni_image>` + `RUN apt-get install
  postgresql-NN-pgaudit` (or equivalent for your base distro).

Recover the failed bootstrap before retrying (Patroni won't overwrite
`pg_data.failed`):
```bash
ansible patroni_nodes -m shell -b -a \
  'docker rm -f patroni; rm -rf /opt/patroni/pgdata.failed /opt/patroni/pgdata'
```
Then fix `patroni_extensions` and re-run `playbooks/patroni/install.yml`.

---

### `'patroni_vip_address' is undefined` at validation stage

**Cause:** `patroni_vip_engine` is set (default: `vip-manager`) but `patroni_vip_address` address
isn't.

**Fix:** add `patroni_vip_address: 10.x.y.z` to your inventory, OR set `patroni_vip_engine: none`
if you're behind an external load balancer and don't need the floating IP.
See [VIP manager choice](../roles/patroni/README.md#vip-manager-choice).

---

### `WAL-G backup fails` after storage credentials rotation

**Cause:** the `walg.env` file in the container isn't refreshed until the
next playbook run rewrites it. Same for the GCS service-account key at
`/opt/walg/gcs/credentials.json` when `walg_storage_type: gcs`.

**Fix:**
```bash
ansible-playbook playbooks/patroni/install.yml -i inventories/<inv>.yml \
  --tags patroni
```
Or just re-run `deploy.yml` — the config-render step is idempotent and will
replace `walg.env` (and the GCS key) with the new creds, then restart patroni
so wal-g picks them up.

---

### `WAL-G on GCS` can't authenticate

**Cause:** wal-g reads the service account from the JSON key file that
`GOOGLE_APPLICATION_CREDENTIALS` points at — not from env vars. The key is
rendered to `/opt/walg/gcs/credentials.json` on the host (0600,
patroni-owned) and bind-mounted read-only into the patroni container at
`/etc/walg/credentials.json`.

**Fix:** check, in order:
```bash
# 1. Key reached the container?
podman exec patroni cat /etc/walg/credentials.json | head -3
# 2. Env var points at it?
podman exec patroni printenv GOOGLE_APPLICATION_CREDENTIALS WALG_GS_PREFIX
# 3. wal-g can actually list the archive?
podman exec patroni /opt/walg/wal-g backup-list
```
An empty file in step 1 means the host file isn't readable by the patroni UID.
A permission error in step 3 means the service account lacks
`roles/storage.objectAdmin` on `walg_gcs_bucket` — wal-g needs list, read,
write, **and** delete (`walg_retention` prunes old backups).

---

### etcd quorum lost after node removal

**Cause:** removed a node from a 2-node cluster, leaving 1 member → no
quorum possible.

**Fix:** don't — the role refuses to remove from a 2-node cluster. Add a
3rd node first, then remove. If you already lost quorum, you'll need to
restore from backup (WAL-G PITR) or do emergency single-node etcd recovery
([etcd docs: "Disaster recovery"](https://etcd.io/docs/v3.5/op-guide/recovery/)).

## RouterOS

### `'ascii' codec can't encode character '—'`

**Cause:** MikroTik's binary API is ASCII-only; comment contains a UTF-8
character (em-dash, etc.).

**Fix:** 0.2.0+ uses ASCII-only separators in default comment templates.
If you override `routeros_address_list_comment`, keep it ASCII.

---

### `Unknown key "_state"` when pruning

**Cause:** pre-0.2.x used an `_state: absent` marker that `community.routeros.api_modify`
doesn't understand.

**Fix:** upgrade to 0.2.0. Pruning now uses `community.routeros.api` with
`remove: <.id>`, the real RouterOS primitive.

## Ansible internals

### `--tags <foo>` runs `Validating arguments against arg spec` on roles I didn't ask for

**Cause:** Ansible auto-generates `validate_argument_spec` tasks for roles
with `meta/argument_specs.yml` and tags them `always` internally — they run
regardless of `--tags`.

**Fix:** expected behavior, not a bug. If the output noise bothers you:
- Use `--skip-tags always` alongside `--tags <foo>` (skips Stage 0 validator
  too, so only do this if your inventory is known good).
- Drop `meta/argument_specs.yml` from the role (loses input validation).
- Add a custom callback plugin to filter those task names (~20 lines of
  Python; see the "Option 2" snippet in earlier discussions).

---

### Role defaults reference an unrelated role's variables

**Cause:** Ansible role defaults are evaluated lazily but only against the
variable scope at read time. A role's defaults aren't visible to other
roles unless the owning role has been invoked.

**Fix:** either:
- Inline the default with `| default('<safe-value>')`.
- Set the variable at inventory or group_vars level (not as a role default).
- Move shared lookups into a role that's always invoked first (e.g. `common`).

## Still stuck?

Collect:
- Ansible version (`ansible --version`)
- Collection version (`ansible-galaxy collection list startechnica.infra`)
- Playbook command and inventory (redact secrets)
- The failing task + its error message (not the full log)

…and file an issue with that payload. The more specific, the faster we can
reproduce.
