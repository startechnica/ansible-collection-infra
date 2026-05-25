# Secrets handling

This collection intentionally accepts plaintext passwords in inventories
(the validator has no "must come from vault" check), because many users
start with quick dev setups. For production, use `ansible-vault` to wrap
everything sensitive.

## What counts as sensitive

| Category | Variables | Where it ends up |
|---|---|---|
| NetBox | `netbox_connect.token` | All API calls in `netbox_lookup` / `netbox_register` |
| vCenter | `vcenter_connect.username`, `vcenter_connect.password` | `instance`, `vcenter_sync` |
| MongoDB admin | `mongodb_admin_password` | `mongodb` role, written to artifacts/admin.password |
| MongoDB app users | `mongodb_databases[*].users[*].password` | `mongodb` role manage_users.yml |
| Patroni | `postgresql_postgres_password`, `postgresql_replication_password` | `patroni` role, written to artifacts/credentials.txt |
| Patroni app users | `patroni_databases[*].users[*].password` | `patroni` role manage_databases.yml |
| S3 backup target | `s3_access_key`, `s3_secret_key` | shared by `mongodb` + `patroni` backup flows |
| Webhook tokens | GitLab webhook tokens if you use the optional gitlab_project role | `gitlab_project` role |

## Recommended file layout

```
inventories/
  group_vars/
    all/
      vcenter.yml          ← public-ish vCenter config (hostname, instance_datacenter)
      vcenter.vault.yml    ← vault-encrypted (username, password)
      netbox.yml           ← public NetBox config (url)
      netbox.vault.yml     ← vault-encrypted (token)
      s3.vault.yml         ← vault-encrypted (s3_access_key, s3_secret_key)
  <project>.yml            ← per-project inventory (instances, scope)
```

## Creating a vault file

```bash
# One-time: set a password file (or use --ask-vault-password every time)
echo 'your-vault-password' > ~/.vault_pass.txt
chmod 600 ~/.vault_pass.txt
# And in ansible.cfg:
#   [defaults]
#   vault_password_file = ~/.vault_pass.txt

# Create encrypted secrets file
ansible-vault create inventories/group_vars/all/s3.vault.yml
```

Inside the file:

```yaml
---
vault_s3_access_key: "AKIA..."
vault_s3_secret_key: "..."
vault_netbox_token: "abc123..."
vault_mongodb_admin_password: "..."
vault_postgres_password: "..."
```

## Referencing vault variables from inventories

Keep the *reference* in the public inventory; the *value* lives in the vault file:

```yaml
# inventories/<your-inventory>.yml (committed to git, safe)
all:
  children:
    cloud_init:
      vars:
        mongodb_admin_password: "{{ vault_mongodb_admin_password }}"
        s3_access_key: "{{ vault_s3_access_key }}"
        s3_secret_key: "{{ vault_s3_secret_key }}"
        netbox_connect:
          url: "https://ipam.example.com"
          token: "{{ vault_netbox_token }}"
          validate_certs: false
```

Ansible resolves the `{{ vault_* }}` references at playbook run-time from the
decrypted vault file.

## Editing an existing vault file

```bash
ansible-vault edit inventories/group_vars/all/s3.vault.yml
```

## Rotating the vault password

```bash
ansible-vault rekey inventories/group_vars/all/*.vault.yml
```

## CI / Automation

- **GitHub Actions / GitLab CI**: add the vault password as a masked secret,
  write it to a file in the runner before the playbook step, and point
  `ANSIBLE_VAULT_PASSWORD_FILE` at it.
- **Don't commit the password file itself** — `.gitignore` covers
  `.vault-password`, `.vault_pass`, `.vault-pass.txt`, etc.

Example GitLab CI snippet:

```yaml
deploy:
  script:
    - echo "$VAULT_PASSWORD" > "$CI_PROJECT_DIR/.vault_pass"
    - export ANSIBLE_VAULT_PASSWORD_FILE="$CI_PROJECT_DIR/.vault_pass"
    - ansible-playbook playbooks/deploy.yml -i inventories/prod.yml
```

## What's auto-generated vs. must be provided

| Secret | Provision | How |
|---|---|---|
| MongoDB admin password | auto-generate if empty | Written to `artifacts/<inv>/mongodb/admin.password`, re-used on subsequent runs |
| Patroni postgres password | auto-generate if empty | Written to `artifacts/<inv>/patroni/credentials.txt` |
| Patroni replication password | empty = TLS client-cert auth (preferred) | No password to leak when empty |
| MongoDB per-database user passwords | you must provide | Use `{{ vault_* }}` |
| Patroni per-database user passwords | you must provide | Use `{{ vault_* }}` |
| TLS certificates | auto-generated | PKI lives on controller under `artifacts/<inv>/<service>/certs/` |
| S3 credentials | you must provide | Use `{{ vault_s3_* }}` |
| NetBox token | you must provide | Use `{{ vault_netbox_token }}` |
| vCenter credentials | you must provide | Use `{{ vault_vcenter_* }}` |

## Inspecting artifacts securely

Everything under `playbooks/artifacts/` is sensitive (generated certs, admin
passwords, per-DB credentials). `.gitignore` excludes the whole directory.
Back it up out-of-band — if you lose the `ca.key`, you can't renew member
certs without rotating the CA, which means a cluster restart.

Recommended backup target: the same S3 bucket as `s3_bucket`, under a
separate `secrets/` prefix, encrypted with GPG or SSE-KMS.

## Audit checklist

- [ ] `.gitignore` entries for vault files are present
- [ ] `ansible-vault view <file>` works from every workstation that needs to run the playbooks
- [ ] No plaintext passwords in any committed inventory (`grep -rE 'password:\s*[^{"]' inventories/`)
- [ ] `playbooks/artifacts/` is backed up somewhere
- [ ] CI runners have the vault password as a masked variable, not a file in git
- [ ] Rotation schedule: TLS certs (auto, via renew-certs), passwords (rotate-passwords), CA cert (explicit rotate_ca=true)
