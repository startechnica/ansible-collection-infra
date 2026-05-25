# Architecture

High-level data flow, role topology, and per-service container layouts.
Diagrams are Mermaid — GitHub / GitLab render them natively.

## Collection scope

```mermaid
flowchart LR
  inv[Inventory YAML] --> setup["playbooks/*/&#95;setup.yml"]
  setup -- enrichment --> nbf[netbox_lookup]
  nbf -- read-only --> nb[(NetBox)]
  nbf --> setup
  setup -- builds groups + artifacts_dir --> hostgroups[patroni_nodes &#47; mongodb_nodes]
  hostgroups --> provision[instance]
  provision -- REST &#47; SOAP --> vc[(vCenter)]
  provision -- register --> nbr[netbox_register]
  nbr -- write --> nb
  provision --> deploy[service role install]
  deploy --> mongo[mongodb role]
  deploy --> pg[patroni role]
  mongo --> artifacts["playbooks/artifacts/&lt;inv&gt;/"]
  pg --> artifacts
  mongo -- optional --> s3[(S3 / MinIO)]
  pg -- optional --> s3
```

The dashed line between `nbf` and `setup` represents a loop: `_setup.yml`
calls `netbox_lookup`, which updates the `instances` fact, which `_setup.yml` then
uses to build `add_host` groups.

## Role topology

```mermaid
flowchart TD
  sp[preflight]
  mongo[mongodb]
  pg[patroni]
  vmp[instance<br/>core: preflight,<br/>validate, deploy,<br/>configure, verify]
  vmpci[instance_cloud_init<br/>inject / poweron /<br/>post_provision / cleanup]
  vmpig[instance_ignition<br/>fcos_prepare / render /<br/>inject / poweron / cleanup]
  nbf[netbox_lookup]
  nbr[netbox_register]
  vcs[vcenter_sync]
  gg[geerlingguy.docker<br/>external galaxy role]

  mongo -. meta dep .-> sp
  pg -. meta dep .-> sp
  vmp -- include_role tasks_from: --> vmpci
  vmp -- include_role tasks_from: --> vmpig
  deploy[[playbooks/deploy.yml]]
  deploy --> nbf --> vmp --> nbr --> gg
  gg --> mongo
  gg --> pg
```

- **`mongodb` / `patroni`** declare explicit meta deps on `preflight`.
- **`instance_cloud_init` / `instance_ignition`** are sub-roles of
  `instance`. They don't expose a `main.yml` entrypoint; `instance`
  includes specific phase tasks via `include_role: tasks_from:`. Split exists
  so the two init modes don't entangle in `instance`'s task tree.
- **`geerlingguy.docker`** is a third-party role from Galaxy — pulled in via
  `requirements.yml` and invoked directly from orchestration playbooks.

## Provisioning flow (`playbooks/deploy.yml`)

```mermaid
sequenceDiagram
  participant C as Controller
  participant NB as NetBox
  participant VC as vCenter
  participant VM as Target VMs

  Note over C: Stage 0: validate (common.validate_inventory)
  C->>C: assert vars (NetBox conn, S3, VIP, scope)

  Note over C: Stage 1: netbox_lookup
  C->>NB: GET /virtualization/virtual-machines/?...
  NB-->>C: tags, ipv4, ipv6, netbox_id
  C->>C: merge into instances fact

  Note over C: Stage 2: instance
  C->>VC: validate folder / datastore / portgroup / template
  C->>VC: deploy_content_library_ovf (async, parallel)
  VC-->>C: VM tasks running
  C->>VC: poll until VMs registered
  C->>VC: configure hardware + inject cloud-init (guestinfo)
  C->>VC: power on
  VC->>VM: boot + cloud-init runs with injected data

  Note over C: Stage 2 (continued): netbox_register
  C->>NB: POST /virtualization/virtual-machines/ (each VM)
  C->>NB: POST /ipam/ip-addresses/ + bind interface
  C->>NB: PATCH primary_ip4

  Note over C: Stage 3: build add_host groups (tag-routing)
  C->>C: mongodb_nodes / patroni_nodes from item.tags

  Note over C: Stage 3.5: SSH wait
  C->>VM: wait_for_connection (cloud-init completed)

  Note over C: Stage 4: Docker
  C->>VM: geerlingguy.docker role

  Note over C: Stage 5-6: MongoDB + Patroni installs
  C->>VM: role execution (serial for cluster init safety)
  Note right of C: Each role registers its own<br/>ports in NetBox on every node.
  C->>NB: POST /ipam/services/ per exposed port

  Note over C: Stage 7: summary
```

## MongoDB — container topology

```mermaid
flowchart LR
  subgraph "MongoDB node"
    mongos["mongos :27017<br/>client entry (sharded)"]
    configsvr["configsvr :27018<br/>cluster metadata (sharded)"]
    mongod["mongod :27019<br/>data"]
    exp["mongodb-exporter :9216"]

    mongos <--> configsvr
    mongos <--> mongod
    exp -. TLS X.509 .-> mongod
  end

  client["client / driver"] -- :27017 TLS --> mongos
  prom[(Prometheus)] -- :9216 --> exp
```

**Replicaset mode** collapses to just the `mongod` container; clients
connect to it directly on 27017.

## Patroni — container topology

```mermaid
flowchart LR
  subgraph "Patroni node (one per VM)"
    etcd["etcd :2379 / :2380<br/>DCS"]
    patroni["patroni<br/>postgres :55432"]
    pgb["pgbouncer :6543<br/>pool"]
    hap["haproxy<br/>5432 → primary<br/>5433 → replicas<br/>7000 → stats"]
    vipm["vip-manager<br/>floating IP"]
    exp["postgres_exporter :9187"]

    etcd -- leader election --> patroni
    patroni --> pgb
    pgb --> hap
    vipm -. reads DCS .-> etcd
    exp -. sslmode=disable :55432 .-> patroni
  end

  client["client"] -- :5432 VIP --> hap
  prom[(Prometheus)] -- :9187 --> exp
```

**Client connection flow**: client → VIP (floats to current primary) →
HAProxy (http-checks Patroni REST API for `/primary`) → PgBouncer → Postgres.

## Artifact directory layout

```mermaid
flowchart TD
  inv["inventories/&lt;project&gt;.yml"]
  art[playbooks/artifacts/&lt;project&gt;/]
  inv -- inventory stem --> art

  subgraph art[playbooks/artifacts/<inventory-stem>/]
    mongo_dir[mongodb/]
    patroni_dir[patroni/]

    subgraph mongo_dir
      admin_pw[admin.password]
      mongo_certs[certs/ca, members, clients]
      mongo_user_creds[&lt;db&gt;-&lt;user&gt;.txt]
    end
    subgraph patroni_dir
      pg_creds[credentials.txt<br/>postgres+replication+pgbouncer]
      patroni_certs_dir[certs/ca, etcd, patroni, haproxy, vip-manager]
      patroni_db_creds[&lt;db&gt;-&lt;user&gt;.txt]
    end
  end
```

Everything under `playbooks/artifacts/` is sensitive (see
[SECRETS.md](SECRETS.md)) and excluded from git.

## TLS PKI flow (MongoDB + Patroni)

```mermaid
sequenceDiagram
  participant C as Controller
  participant Art as playbooks/artifacts/
  participant N1 as Node1
  participant N2 as Node2

  Note over C: Generate CA (once per cluster)
  C->>C: openssl_privatekey (ca.key, ECDSA)
  C->>C: x509_certificate selfsigned (ca.crt)
  C->>Art: save ca.key + ca.crt

  Note over C: Per-host member certs
  loop each node
    C->>C: openssl_privatekey (<node>-mongod.key)
    C->>C: openssl_csr with SAN = [node IP, hostname, 127.0.0.1]
    C->>C: x509_certificate ownca (signed by CA)
    C->>Art: save per-host cert
  end

  Note over C: Client certs (healthcheck, exporter)
  C->>C: generate client certs with DN = {O, OU=...Client, CN=...}
  C->>Art: save client certs

  Note over C: Distribute
  C->>N1: copy ca.pem + node1 certs + client certs → /etc/mongo/ssl/
  C->>N2: copy ca.pem + node2 certs + client certs → /etc/mongo/ssl/
  Note over N1,N2: containers mount ssl dir read-only
```

CA keys live only on the controller (`playbooks/artifacts/<inv>/<svc>/certs/`)
unless explicitly distributed. On cert rotation (renew-certs playbook), a
rolling restart picks up the new leaf certs — CA itself is preserved unless
`rotate_ca=true`.

## Day-2 action dispatch

Both `mongodb` and `patroni` roles use an `<role>_action` variable to
select sub-flows from `main.yml`:

```mermaid
flowchart LR
  play[playbook] --> role[role: mongodb<br/>vars: mongodb_action: X]
  role --> main[tasks/main.yml]
  main -- provision --> p[provision full stack]
  main -- backup --> b[tasks/backup.yml]
  main -- status --> s[tasks/status.yml]
  main -- verify-backup --> vb[verify_backup scenario]
  main -- uninstall --> u[tasks/uninstall.yml]
  main -- ... --> other[...]
```

Every day-2 playbook includes the same two plays:
1. `hosts: localhost` — runs `_setup.yml` (NetBox enrichment, dynamic groups, artifacts_dir).
2. `hosts: <service>_nodes` — includes the role with `<role>_action: <action>`.

This is why adding a new action is just: (a) add the task file, (b) add the
conditional include in `main.yml`, (c) add a playbook wrapper. No new
dispatching mechanism.

## See also

- [docs/SECRETS.md](SECRETS.md) — vault + credential handling
- [README.md](../README.md) — setup, requirements, quickstart
- [roles/mongodb/README.md](../roles/mongodb/README.md) — MongoDB-specific details
- [roles/patroni/README.md](../roles/patroni/README.md) — Patroni-specific details
- [roles/netbox_lookup/README.md](../roles/netbox_lookup/README.md) — NetBox lookup modes
