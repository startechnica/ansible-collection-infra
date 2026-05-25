# Examples

Reference shapes and patterns for consuming `startechnica.infra`. Copy the
pieces you want into your own repo; nothing here is invoked by the
collection at runtime.

## Contents

| File | Purpose |
|---|---|
| `inventory.minimal.yml` | Smallest working inventory — vCenter creds, 3 VMs, one service |
| `inventory.full.yml` | Production-shaped inventory — vault, NetBox, backups to S3, firewall, RouterOS |
| `vault.yml` | Vault-encrypted secrets file shape (decrypt reference) |
| `crontab.example` | Reference crontab for scheduled backups + verify + cert-renew |
| `gitlab-ci.example.yml` | Starter `.gitlab-ci.yml` for deploying from GitLab with vault |

## Suggested repo layout for consumers

```
your-deploy-repo/
├── requirements.yml                  # installs this collection
├── inventories/
│   ├── <site-a>.yml               # per-site inventory
│   ├── <site-b>.yml
│   └── group_vars/
│       └── all/
│           ├── vcenter.yml           # vault-encrypted
│           ├── netbox.yml            # vault-encrypted
│           └── routeros.yml          # vault-encrypted
└── .gitlab-ci.yml                    # scheduled jobs
```

No need to clone the collection into your repo — `ansible-galaxy collection
install -r requirements.yml` pulls it into `~/.ansible/collections/`.

## First deployment

```bash
# Install
ansible-galaxy collection install startechnica.infra

# Validate (no side effects)
ansible-playbook startechnica.infra.deploy \
  -i inventories/mysite.yml --tags validate

# Deploy
ansible-playbook startechnica.infra.deploy \
  -i inventories/mysite.yml --ask-vault-pass
```

## Day-2 operations

```bash
ansible-playbook playbooks/mongodb/backup.yml        -i inventories/mysite.yml
ansible-playbook playbooks/mongodb/verify-backup.yml -i inventories/mysite.yml
ansible-playbook playbooks/patroni/status.yml        -i inventories/mysite.yml
ansible-playbook playbooks/patroni/switchover.yml    -i inventories/mysite.yml -e target_node=node-02
```

Schedule these via `crontab.example` or the `gitlab-ci.example.yml`.
