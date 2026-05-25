# firewall

OS-agnostic declarative host firewall policy. Applies UFW rules on
Debian/Ubuntu, firewalld rules on RHEL/Rocky/Alma/Fedora, no-op on anything
else. Default off — opt in by setting `firewall_enabled: true`.

## Usage

```yaml
# inventories/<name>.yml
firewall_enabled: true
firewall_trusted_sources:
  - 10.147.0.0/16                       # management / monitoring CIDRs
firewall_rules:                         # extra rules beyond auto-discovery
  - { port: 9100, protocol: tcp, comment: node_exporter }
  - { port: 3000, protocol: tcp, source: "10.0.0.0/8", comment: grafana }
```

Then `deploy.yml` Stage 7 applies the policy, or invoke standalone:

```bash
ansible-playbook playbooks/deploy.yml -i inventories/<inv>.yml --tags firewall
```

## What it opens automatically

1. **SSH** — `ansible_port` (default 22) is always allowed, preventing
   lockout mid-apply.
2. **Cluster service ports** — when
   `firewall_include_service_ports: true` (default), the role reads
   `mongodb_services` / `patroni_services` host facts published by those
   roles' startup tasks and allows every port they describe. Means you
   don't have to hand-enumerate mongos:27017, mongod:27019, configsvr:27018,
   haproxy:5432/5433, pgbouncer:6543, patroni-api:8008, etcd:2379, exporters, etc.
3. **`firewall_rules`** — everything you declare explicitly, layered on top.
4. **`firewall_trusted_sources`** — CIDRs granted blanket inbound.

## Variables

Full spec in [meta/argument_specs.yml](meta/argument_specs.yml).

| Variable | Default | Purpose |
|---|---|---|
| `firewall_enabled` | `false` | Master switch; role is no-op while false. |
| `firewall_default_incoming` | `deny` | `deny` / `allow` / `reject` |
| `firewall_default_outgoing` | `allow` | `deny` / `allow` / `reject` |
| `firewall_rules` | `[]` | List of `{port, protocol, source?, comment?}` |
| `firewall_trusted_sources` | `[]` | Allow-everything CIDRs |
| `firewall_include_service_ports` | `true` | Pull ports from `mongodb_services` + `patroni_services` facts |
| `firewall_service_source_map` | scoped defaults (see below) | Map of service-name → allowed source CIDRs; restricts the matching rule to those sources only |

### Source restrictions

By default the role tightens five service ports so they're only reachable
from the IPs that legitimately need them:

| Service | Port | Allowed sources |
|---|---|---|
| `ssh-trusted` | `ansible_port` (22) | trusted sources — controller IP (auto) + `firewall_trusted_sources`. Always on. |
| `mongod` | 27018 | `groups['mongodb_nodes']` (replica members talk to each other) |
| `configsvr` | 27019 | `groups['mongodb_nodes']` (config replicaset) |
| `patroni-api` | 8008 | `groups['patroni_nodes']` (HAProxy lives there and probes `/master` `/replica`) |
| `etcd-client` | 2379 | `groups['patroni_nodes']` (etcd colocated with Patroni) |

A second SSH rule named `ssh-any` (port 22 from any source) is emitted
alongside `ssh-trusted` and toggled via `firewall_ssh_open_to_any` (default
`true`). Set it `false` + re-run to lock SSH down to trusted sources only,
or flip the live rule manually mid-maintenance — the trusted-source rules
survive either way:

```bash
# Lock SSH down (trusted-only) — instant, no Ansible re-run needed
ufw delete allow 22                                # ufw
firewall-cmd --remove-port=22/tcp --permanent      # firewalld
                                                   # nft: rebuilds on next role run
```

Override piecemeal — e.g. open `patroni-api` to a monitoring subnet too:

```yaml
firewall_service_source_map:
  patroni-api: "{{ groups['patroni_nodes'] | map('extract', hostvars, 'ansible_host') | list + ['10.0.99.0/24'] }}"
```

Set the whole map to `{}` to revert to the legacy "open to any" behavior
on those ports.

## Implementation

- [tasks/main.yml](tasks/main.yml) — assembles `_fw_rules` then dispatches
  on `ansible_os_family`.
- [tasks/ufw.yml](tasks/ufw.yml) — installs ufw, sets default policies,
  iterates the rule list, enables.
- [tasks/firewalld.yml](tasks/firewalld.yml) — installs firewalld, sets
  zone target, uses rich rules for source-scoped ports.

Both backends are idempotent — re-running the role without changes produces
no `changed:` results.

## What this role does NOT do

- **Network ACLs** — cloud/switch-level. Use your cloud provider's security
  groups or the `routeros` role in this collection (for MikroTik uplinks).
- **Host intrusion detection** — fail2ban, crowdsec, etc.
- **Container network policy** — Docker's `--iptables` manages its own
  rules separately. UFW/firewalld rules in this role apply to the host's
  exposed ports (published container ports and host services).

## Verifying

```bash
# UFW (Debian/Ubuntu)
ansible <inv> -a "ufw status verbose" -b

# firewalld (RHEL family)
ansible <inv> -a "firewall-cmd --list-all" -b
```

## Troubleshooting

- **Tightening sources on an already-deployed host** — UFW and firewalld
  don't auto-purge existing rules. If a host previously had `27018 from
  any` and you're now scoping it to `mongodb_nodes`, the old "from any"
  rule remains and the new scoped rule is redundant. Reset once:
  - UFW: `ufw --force reset && ufw --force enable`, then re-run the role.
  - firewalld: `firewall-cmd --permanent --remove-port=27018/tcp` (and the
    other affected ports), `firewall-cmd --reload`, then re-run.
  - nftables (FCOS): nothing to do — the role recreates `inet startechnica`
    from scratch on each apply.
- **"rules applied but connection still refused"** — Docker publishes
  container ports via its own iptables chain (`DOCKER`) which bypasses UFW's
  input chain. Our rule list opens the port on the host; Docker handles the
  container-level routing. If you explicitly want UFW to control the
  Docker-published port, see `ufw-docker` or set `iptables=false` on the
  Docker daemon.
- **"firewalld wipes my zone target every apply"** — expected on first
  run (sets target to DROP when `firewall_default_incoming: deny`).
  Subsequent runs are idempotent.

## See also

- [docs/TROUBLESHOOTING.md](../../docs/TROUBLESHOOTING.md)
- [roles/routeros](../routeros/) — upstream firewall (MikroTik) address-list
  management that complements the host-level policy here.
