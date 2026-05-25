# routeros

Pushes cluster VM IPs (plus the Patroni VIP if set) to a named entry in
`/ip firewall address-list` on a MikroTik RouterOS device. Then you write
RouterOS firewall / NAT / mangle rules that reference the list by name
instead of individual IPs, and they stay in sync with the cluster
automatically.

Default off — opt in by setting `routeros_enabled: true`.

## Usage

```yaml
# inventories/<name>.yml
routeros_enabled: true
routeros_host: 10.0.0.1                 # MikroTik management IP
routeros_user: ansible
routeros_password: "{{ vault_routeros_password }}"
routeros_address_list: db-<your-project>   # the list name on the MikroTik
# routeros_prune_stale: true            # remove entries no longer in instances (opt-in)
```

Run via `deploy.yml` Stage 8 (automatic), or standalone:

```bash
ansible-playbook playbooks/deploy.yml -i inventories/<inv>.yml --tags routeros
```

The role is `hosts: localhost`-safe — it connects to the MikroTik API from
the controller via librouteros. No agent required on the MikroTik side.

## What gets pushed

1. **Every cluster node** (from `groups['cluster_nodes']`) — the `ansible_host`
   IP lands in the address-list with comment `<hostname> - <routeros_address_list_comment>`.
2. **The Patroni VIP** — if `patroni_vip_address` is set, added with comment `VIP - …`.
3. **`routeros_extra_entries`** — any ad-hoc entries you add.

## Required RouterOS user permissions

Minimum policies: **`api, read, write`**.

```rsc
/user group
add name=ansible-firewall policy=api,read,write,!local,!telnet,!ssh,!ftp,!reboot,!policy,!test,!winbox,!password,!web,!sniff,!sensitive,!romon,!rest-api,!dude,!tikapp

/user
add name=ansible group=ansible-firewall password="..." \
    address=10.147.8.0/24 comment="startechnica.infra deployer"
```

See [docs/TROUBLESHOOTING.md](../../docs/TROUBLESHOOTING.md) for auth-scope
hardening (restrict by source CIDR, rotate passwords).

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `routeros_enabled` | `false` | Master switch. |
| `routeros_host` | `""` | MikroTik management IP or hostname. |
| `routeros_user` / `routeros_password` | `""` | API credentials (vault the password). |
| `routeros_port` | `0` (module default: 8729 TLS / 8728 plain) | Override API port. |
| `routeros_tls` | `true` | Use HTTPS API (recommended). |
| `routeros_validate_certs` | `false` | Default off — RouterOS ships with self-signed certs. |
| `routeros_address_list` | `startechnica` | Name of the list on the MikroTik. Override per-deployment. |
| `routeros_address_list_comment` | `"Managed by startechnica.infra"` | Comment suffix on each entry. |
| `routeros_include_instances` | `true` | Push cluster VM IPs. |
| `routeros_include_vip` | `true` | Push `patroni_vip_address` if set. |
| `routeros_extra_entries` | `[]` | List of `{address, comment?}`. |
| `routeros_prune_stale` | `false` | Remove entries under this list that aren't in `instances` anymore. Off by default for safety. |

Full spec in [meta/argument_specs.yml](meta/argument_specs.yml).

## Safety — why pruning is opt-in

The underlying `community.routeros.api_modify` can only scope
"remove entries not in my list" at the `path` level (all of
`/ip firewall address-list`) — not by list name. Using the module's
built-in remove mode would wipe entries from **other** address-lists too,
which isn't what anyone wants.

So pruning is a second pass (`tasks/prune.yml`):

1. `api` with `extended_query` filtered on `list == <our_list>` → fetches only our
   list's current entries server-side (avoids the `query:` string-parsing quirks).
2. Diff against our desired set.
3. `api` with `remove: <.id>` on each orphan.

Other lists on the MikroTik are never touched. Still, it's opt-in —
set `routeros_prune_stale: true` only after you're confident this role is
the sole owner of `routeros_address_list`.

## Example RouterOS rule consuming the list

```rsc
# Allow cluster members through the firewall to a backend service:
/ip firewall filter
add chain=forward action=accept \
    src-address-list=db-<your-project> dst-address=10.99.0.10 \
    comment="DB cluster → app tier"

# Or use for DNS policy, logging, mangle, NAT, bandwidth limiting, etc.
```

## Implementation

- [tasks/main.yml](tasks/main.yml) — orchestrates preflight → build_entries → apply → optional prune.
- [tasks/preflight.yml](tasks/preflight.yml) — asserts connection vars are set.
- [tasks/build_entries.yml](tasks/build_entries.yml) — iterates `cluster_nodes` (falling back to raw `instances`) + VIP + extras to build `_routeros_entries`.
- [tasks/apply.yml](tasks/apply.yml) — `api_modify` with `handle_entries_content: ignore`. Adds/updates our entries, leaves unrelated entries alone.
- [tasks/prune.yml](tasks/prune.yml) — server-side-filtered fetch → diff → per-ID remove.

## Dependencies

- Collection: `community.routeros >= 3.0.0` (declared in `galaxy.yml`).
- Python: `librouteros >= 3.2.1` on the controller (declared in
  `requirements.txt`).

## Gotchas

- **ASCII-only comments** — RouterOS's binary API is ASCII; don't put
  em-dashes, accented characters, or emoji in `routeros_address_list_comment`.
- **Address-list names are not unique** — `/ip firewall address-list` can
  have two entries with the same list name but different addresses, or the
  same address in two different lists. The role scopes strictly to
  `routeros_address_list` for both read and write.
- **Running with `--tags routeros` alone** requires Stages 1 and 3 to also
  run (netbox + build_host_groups) — those are tagged with `routeros` so
  they pull in as prerequisites.

## See also

- [docs/TROUBLESHOOTING.md](../../docs/TROUBLESHOOTING.md)
- [roles/firewall](../firewall/) — host-level UFW/firewalld counterpart.
- [community.routeros collection docs](https://docs.ansible.com/ansible/latest/collections/community/routeros/)
