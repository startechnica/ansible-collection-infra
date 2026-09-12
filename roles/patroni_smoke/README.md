# patroni_smoke

Read-only, post-deployment health verification for a running Patroni PostgreSQL
cluster. The role imports the Patroni role's exported variables, so it evaluates
the deployed topology with the same ports, feature flags, TLS settings, and VIP
strategy used during installation. It does **not** install, reconfigure, restart,
fail over, or rotate credentials on the cluster.

Use `startechnica.infra.patroni_smoke` after deployment and after maintenance to
produce one aggregated PASS/FAIL report rather than stopping at the first failed
probe.

## What it verifies

| Area | Checks | When it applies |
|---|---|---|
| Containers | `etcd`, `patroni`, and `haproxy` are present and running on every node. The selected VIP implementation is added dynamically: `vip-manager` for `patroni_vip_engine: vip-manager`, `keepalived` for `patroni_vip_engine: keepalived`, and neither when no `patroni_vip_address` is configured. | Always for core containers; optional services are feature-gated. |
| etcd | Per-member TLS endpoint health and endpoint-status JSON; the summary requires exactly one leader. | `patroni_smoke_check_etcd` |
| Patroni | `patronictl list --format json`, expected member count, one leader (or standby leader), REST `/health`, and replica-lag threshold. | `patroni_smoke_check_patroni` |
| PostgreSQL | VIP TCP reachability, `SELECT 1`, primary is not in recovery, replication HBA rules, then a write through the VIP followed by replica reads. The smoke table is removed in an `always` block. | Requires a configured VIP and PostgreSQL credentials. |
| Managed databases | Inventory-declared databases, login users, and database owners match live PostgreSQL. If `patroni_databases: []`, audits that no managed databases or login users remain. | `patroni_smoke_check_users_and_dbs` |
| HAProxy / PgBouncer | HAProxy stats endpoint and primary/replica backend health; PgBouncer listener on port `6432`. | PgBouncer is checked only when enabled. |
| VIP | Exactly one node owns `patroni_vip_address`; detects split-brain VIP ownership without forcing failover. | Requires a configured VIP. |
| Backup / metrics | WAL-G backup list and `walg-cron.timer`; PostgreSQL exporter metrics endpoint. | Only when the relevant feature is enabled. |
| TLS | Patroni server and CA certificates can be read. On the controller, generates a scratch CSR, signs it with the local CA, verifies its chain, and validates its SAN. The deployed certificates are never changed. | `patroni_smoke_check_tls` |

Checks are collected even when an individual probe fails. `summary.yml` reports
all detected failures and marks the play failed only after collection completes.

## Requirements

- A previously deployed Patroni cluster with the `patroni_nodes` group built
  from inventory `instances`.
- Controller access to the collection dependencies, including `community.crypto`,
  `community.postgresql`, and `containers.podman`.
- Controller access to `patroni_local_certs_dir` when TLS checking is enabled;
  the controller-side CA must contain `ca/ca.crt` and `ca/ca.key`.
- A PostgreSQL client dependency on the controller for database checks. The
  deployment's `preflight` role normally prepares this environment.

## Usage

Run the collection playbook directly:

```bash
ansible-playbook startechnica.infra.patroni_smoke \
  -i inventories/<inventory>.yml --ask-vault-pass
```

Or invoke the repository helper:

```bash
./scripts/smoke.sh inventories/<inventory>.yml --patroni-only --ask-vault-pass
```

The equivalent source-tree wrapper is `playbooks/patroni_smoke.yml`. It first
runs `patroni/_setup.yml` on the controller to build `patroni_nodes`, then runs
this role on those hosts with privilege escalation.

## Configuration

All role-specific settings can be overridden through inventory, group variables,
or play variables.

| Variable | Default | Purpose |
|---|---:|---|
| `patroni_smoke_enabled` | `true` | Master switch. `false` makes the role a no-op. |
| `patroni_smoke_max_replica_lag_ms` | `5000` | Maximum acceptable Patroni replica lag. |
| `patroni_smoke_replication_wait_seconds` | `10` | Delay between the smoke write and replica reads. |
| `patroni_smoke_required_containers` | `etcd`, `patroni`, `haproxy` | Always-required containers. The correct VIP container is added automatically; do not list both `vip-manager` and `keepalived`. |
| `patroni_smoke_optional_containers` | `pgbouncer`, `postgres-exporter`, `walg-init` | Feature-gated container checks. |
| `patroni_smoke_table` | timestamped name | Temporary table used for the end-to-end write/replication probe. |

Each check family has an independent switch. For example, this runs all normal
checks except the controller-side TLS signing probe:

```yaml
patroni_smoke_check_tls: false
```

Available flags are:

```yaml
patroni_smoke_check_containers: true
patroni_smoke_check_etcd: true
patroni_smoke_check_patroni: true
patroni_smoke_check_postgres: true
patroni_smoke_check_haproxy: true
patroni_smoke_check_pgbouncer: true
patroni_smoke_check_vip: true
patroni_smoke_check_walg: true
patroni_smoke_check_exporter: true
patroni_smoke_check_tls: true
patroni_smoke_check_users_and_dbs: true
```

## Operational notes

- Run after a successful deployment, a rolling restart, switchover, certificate
  renewal, backup configuration change, or other maintenance that may affect
  availability.
- Do not run during a restore, planned failover exercise, or in-progress
  deployment. The role checks normal steady-state health and will flag expected
  transitional conditions.
- The PostgreSQL end-to-end probe writes a temporary table, and the TLS probe
  creates temporary controller-side files below
  `<patroni_local_certs_dir>/_smoke_new_cert/`. Both are cleaned up by the role.
- A disabled VIP skips VIP-dependent PostgreSQL and VIP ownership checks. This
  is expected for clusters intentionally configured without a floating IP.
- The active VIP implementation is selected from `patroni_vip_engine` and
  `patroni_vip_address`; a `keepalived` result is only expected when keepalived
  is the configured strategy.

See [the smoke-test guide](../../playbooks/test/README.md) for shared runbook
advice and the full check inventory.
