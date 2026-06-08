# grafana_alloy

Deploy [Grafana Alloy](https://grafana.com/docs/alloy/) as a single container
per host to collect and ship:

- **logs** → Loki (systemd journal via `loki.source.journal`)
- **metrics** → Prometheus (host/node metrics via `remote_write`)
- **traces** → Tempo (OTLP receiver → OTLP exporter)

Each pipeline is independently toggled. The `config.alloy` is assembled from
role variables; you do not hand-write it.

Runs via **docker** (compose) or **podman** (Quadlet under
`/etc/containers/systemd/`), selected by `container_engine` — the same
dual-engine pattern as the `mongodb` / `patroni` roles, so it works on both
cloud-init (Ubuntu) and Ignition (FCOS) hosts.

## Secrets

Backend passwords are **never written into `config.alloy`**. When any
`*_password` is set, the role renders an `EnvironmentFile` (`alloy.env`, mode
`0600`, root-only) and `config.alloy` reads each secret with `sys.env(...)`.
Supply the passwords from Ansible Vault:

```yaml
grafana_alloy_loki_password: "{{ vault_grafana_cloud_logs_token }}"
```

## Quick start

```yaml
- hosts: db_nodes
  become: true
  roles:
    - role: startechnica.infra.grafana_alloy
      vars:
        grafana_alloy_enabled: true
        grafana_alloy_cluster_label: myproject-pg

        # logs → Loki
        grafana_alloy_loki_url: "https://logs-prod-012.grafana.net/loki/api/v1/push"
        grafana_alloy_loki_username: "123456"
        grafana_alloy_loki_password: "{{ vault_grafana_cloud_logs_token }}"

        # metrics → Prometheus
        grafana_alloy_prometheus_url: "https://prometheus-prod-01.grafana.net/api/prom/push"
        grafana_alloy_prometheus_username: "654321"
        grafana_alloy_prometheus_password: "{{ vault_grafana_cloud_metrics_token }}"

        # traces → Tempo (disable if you have no tracing)
        grafana_alloy_tempo_url: "tempo-prod-04.grafana.net:443"
        grafana_alloy_tempo_username: "789012"
        grafana_alloy_tempo_password: "{{ vault_grafana_cloud_traces_token }}"
```

Disable a pipeline you don't need:

```yaml
grafana_alloy_traces_enabled: false
```

## Day-2 actions

```bash
# status
ansible-playbook ... -e grafana_alloy_action=status

# restart (after changing config outside the role, or to bounce)
ansible-playbook ... -e grafana_alloy_action=restart

# uninstall (keeps data dir unless destroy flag is set)
ansible-playbook ... -e grafana_alloy_action=uninstall
ansible-playbook ... -e grafana_alloy_action=uninstall -e grafana_alloy_destroy_data=true
```

On a normal re-run, a changed `config.alloy` / `alloy.env` triggers the
`restart grafana_alloy` handler so Alloy reloads with the new settings.

## What it mounts

The container uses host networking and bind-mounts (read-only) the host
journal, `/etc/machine-id`, and `/proc`, `/sys`, `/` (exposed to the unix
exporter as `/host/proc`, `/host/sys`, `/rootfs`). The Alloy web UI is served
on `http://<host>:{{ '{{' }} grafana_alloy_listen_port {{ '}}' }}` (default 12345).

## Key variables

| Variable | Default | Purpose |
|---|---|---|
| `grafana_alloy_enabled` | `false` | Master switch |
| `grafana_alloy_version` | `v1.12.1` | Image tag |
| `grafana_alloy_logs_enabled` | `true` | journald → Loki |
| `grafana_alloy_metrics_enabled` | `true` | node metrics → Prometheus |
| `grafana_alloy_traces_enabled` | `true` | OTLP → Tempo |
| `grafana_alloy_*_url` | `""` | Endpoint per pipeline (required when its pipeline is enabled) |
| `grafana_alloy_*_username` / `*_password` | `""` | Basic auth (password via `alloy.env`/`sys.env`) |
| `grafana_alloy_tempo_protocol` | `grpc` | `grpc` or `http` OTLP transport |
| `grafana_alloy_cluster_label` | `""` | `cluster` label on all telemetry |
| `grafana_alloy_listen_port` | `12345` | Alloy HTTP server / UI |
| `grafana_alloy_mem_limit_mb` | `256` | Container memory limit |

See [meta/argument_specs.yml](meta/argument_specs.yml) for the full schema.
