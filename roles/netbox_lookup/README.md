# netbox_lookup

Queries NetBox and produces generic VM facts consumable by `instance`,
`netbox_register`, and the service roles (`mongodb`, `patroni`). Use it as a
role dependency or inline via `include_role` / the `_setup.yml` pre-plays.

## Three modes

The role auto-detects which mode to run based on the shape of the `instances` fact
(see [tasks/main.yml](tasks/main.yml)):

| Shape of `instances` | Mode | Effect |
|---|---|---|
| `[{name: foo}, ...]` (some entries without `ipv4`) | **Enrich** | Per-VM GET to NetBox; fill in `ipv4` / `ipv6` / `netbox_id` / `tags` on each VM that's missing them |
| `[{netbox_id: 42}, ...]` (ID only, no name) | **Resolve** | Fetch full VM details by ID (name, IP, CPU, memory, disk, role, …) |
| `[]` or unset | **Query** | Build the entire `instances` list from NetBox using the `netbox_lookup_*` filters |

Modes are additive — you can mix forms (some VMs with IPs, some without, some
with only `netbox_id`) and the role resolves each correctly.

## Inputs

### Connection (required)

```yaml
netbox_connect:
  url: "https://netbox.example.com"
  token: "{{ vault_netbox_token }}"
  validate_certs: false
```

Put this in `group_vars/all/netbox.yml` (encrypted with `ansible-vault`) so all
roles that touch NetBox share one config.

### Scope filters — Enrich mode

Applied when per-VM lookups happen:

| Var | Default | Purpose |
|---|---|---|
| `instance_cluster_name` | unset | vCenter cluster name. Resolved to `cluster_id`, used to scope the VM query so name collisions across clusters don't return the wrong ID. |
| `instance_folder` | unset | Fallback used when `instance_cluster_name` is empty. Filters VMs where the `vcenter_folder` custom field matches. |
| `netbox_vrf` | `""` | VRF **name**. Resolved to `vrf_id` and used when fetching IPs — for VMs with multiple IPs across VRFs (e.g., management vs. data), this picks the right one. |
| `netbox_lookup_interface` | `ens192` | Informational — documents which interface IPs are expected to come from. |

### Query filters — Query mode (build whole list from NetBox)

Combined with AND logic. All optional — omit or leave empty to skip.

| Var | NetBox filter |
|---|---|
| `netbox_lookup_cluster` | `cluster=<name>` |
| `netbox_lookup_site` | `site=<slug>` |
| `netbox_lookup_tenant` | `tenant=<slug>` |
| `netbox_lookup_role` | `role=<slug>` |
| `netbox_lookup_status` | `status=<value>` (e.g. `active`, `planned`) |
| `netbox_lookup_tags` | `tag=<csv>` (all must match) |
| `netbox_lookup_custom_fields` | `cf_<key>=<value>` (map) |

### Output control

| Var | Default | Purpose |
|---|---|---|
| `netbox_lookup_enabled` | `true` | Master on/off switch. |
| `netbox_lookup_set_instances` | `true` | Query mode only: overwrite `instances` with the built list. When `false`, only sets `netbox_vms` (raw response) — lets other code decide how to map fields. |

## Outputs

Sets/updates the `instances` fact. Per-VM, fields that may appear:

| Field | Source | Modes |
|---|---|---|
| `name` | inventory or NetBox | all |
| `netbox_id` | NetBox (`vm.id`) | enrich, resolve, query |
| `ipv4` | NetBox IP (`family.value == 4`), VRF-filtered if `netbox_vrf` set | enrich, resolve, query |
| `ipv6` | NetBox IP (`family.value == 6`), VRF-filtered if `netbox_vrf` set | enrich, resolve, query |
| `tags` | `[slug, …]` (NetBox tag slugs, lowercase) | enrich, resolve, query |
| `cpu` | `vm.vcpus` | resolve, query |
| `memory_mb` | `vm.memory` | resolve, query |
| `disks` | `[{size_gb: vm.disk}]` | resolve, query |
| `status` | `vm.status.value` | resolve, query |
| `platform` | `vm.platform.slug` | resolve, query |
| `role` | `vm.role.slug` | resolve, query |
| `site` | `vm.site.slug` | resolve, query |
| `tenant` | `vm.tenant.slug` | resolve, query |
| `cluster` | `vm.cluster.name` | resolve, query |
| `custom_fields` | `vm.custom_fields` | resolve, query |

Also available:

- **`netbox_vms`** — raw NetBox API response (only in Query mode when `netbox_lookup_set_instances: false`).
- **`netbox_vm_list`** — the structured build output in Query mode; same shape as `instances`.

## Precedence (who wins if data is set in multiple places)

1. **Per-VM** values in the inventory (`instances: [{name: foo, ipv4: 1.2.3.4/24, tags: [x]}]`)
2. **Inventory-level** overrides (`instance_tags:` applies when `item.tags` is not set on the VM)
3. **NetBox**-returned values — merged in for any field not already set

Meaning: if you set `ipv4` or `tags` directly in the inventory, the role leaves them alone.

## Failure modes (fail-loudly)

- Missing connection (`netbox_connect.url` or `.token` empty) → immediate assert fail.
- `instance_cluster_name` set but not found in NetBox → fails listing available cluster names, so you can fix a typo quickly.
- `netbox_vrf` set but not found → fails with the VRF name you passed.
- Enrich mode runs but a VM can't be matched in NetBox (missing or outside the cluster/folder scope) → fails with a list of unresolved names and a hint pointing at `instance_cluster_name` / `instance_folder` / the expected interface.

## Gotchas

- **NetBox filter inconsistency** — `/virtualization/virtual-machines/?cluster=<name>` wants the **slug**, not the display name; `/ipam/ip-addresses/?vrf=<name>` doesn't accept name at all. The role resolves name → ID first then filters by `cluster_id` / `vrf_id` to work around this.
- **Tag slugs are lowercase** — NetBox auto-slugifies tag names. A tag named `MongoDB` becomes slug `mongodb`; that's what lands in `item.tags` and what `build_host_groups.yml` matches against (`'mongodb' in item.tags`).
- **Unscoped lookups can pick the wrong VM** — if `instance_cluster_name` and `instance_folder` are both empty *and* VM names aren't globally unique across NetBox, the enrichment picks `results[0]` without warning. Always set at least one scope.
- **IP-lookup runs per VM, not per interface** — the role fetches all IPs on the VM and picks the first IPv4 and first IPv6 matching the VRF filter. If a VM has multiple IPv4s in the same VRF (e.g., primary + floating), which one is "first" depends on NetBox's default ordering. Set `netbox_vrf` to narrow, or use `primary_ip` via custom tasks if you need a deterministic pick.

## Examples

### Enrich VMs (names known, IPs auto-filled)

```yaml
instances:
  - name: app1-db
  - name: app2-db
  - name: app3-db

instance_cluster_name: vCenter-Cluster          # scope for name resolution
netbox_vrf: "vrf-db" # pick IPs in this VRF
```

After the role runs:

```yaml
instances:
  - name: app1-db
    netbox_id: 969
    ipv4: 10.147.8.58/24
    tags: [mongodb, patroni]
  - ...
```

### Mixed — some VMs with IPs in inventory, others enriched

```yaml
instances:
  - name: app1-db
    ipv4: 10.147.8.56/24   # kept as-is
  - name: app2-db          # enriched from NetBox
```

### Query mode — build the full list from NetBox

```yaml
instances: []                          # or leave unset

netbox_lookup_cluster: Deviruchi
netbox_lookup_role: database
netbox_lookup_tags: [production]
```

After the role runs, `instances` contains every VM in Deviruchi + database + production.

## See also

- [roles/netbox_register](../netbox_register/) — writes VMs and IPs *back* into NetBox (complementary).
- [roles/common/tasks/build_host_groups.yml](../common/tasks/build_host_groups.yml) — consumes `instances` + `tags` to route hosts to service groups.
- [roles/common/tasks/validate_inventory.yml](../common/tasks/validate_inventory.yml) — asserts the inventory side of these inputs is consistent (run via `deploy.yml --tags validate`).
