# Contributing

Thanks for wanting to improve `startechnica.infra`. Here's the shortest
path from "cloned the repo" to "PR ready to review."

## Dev setup

```bash
git clone https://gitlab.com/skydata/ansible-collection-infra.git
cd ansible-collection-infra

# 1. Python deps (controller-side)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Ansible collections + roles
ansible-galaxy collection install -r requirements.yml
ansible-galaxy role install -r requirements.yml

# 3. Lint + syntax tooling
pip install 'ansible-core>=2.16' 'ansible-lint>=24' yamllint \
            'molecule>=24' 'molecule-plugins[docker]'
```

## Running checks locally

```bash
# Lint
yamllint .
ansible-lint

# Syntax-check all playbooks against a sample inventory
for pb in playbooks/*.yml playbooks/*/*.yml; do
  ansible-playbook --syntax-check -i inventories/example-cloud-init.yml "$pb"
done

# Molecule — one role at a time (7 testable scenarios)
cd roles/preflight && molecule test
# repeat for: common, firewall, netbox_lookup, netbox_register,
#             vcenter_sync, instance
```

CI (`.github/workflows/ci.yml`) runs all of the above on every push; local
checks are the fast feedback loop before you push.

## Repository layout

```
.
├── galaxy.yml                  # Collection metadata
├── requirements.txt            # Python (controller)
├── requirements.yml            # Ansible collections + roles
├── ansible.cfg                 # Defaults (interpreter discovery, etc.)
├── docs/                       # ARCHITECTURE, SECRETS, UPGRADING, TROUBLESHOOTING
├── examples/                   # Reference inventory + playbook shapes
├── inventories/                # Site-specific (excluded from collection build)
├── playbooks/                  # Orchestration entrypoints (deploy.yml, day-2 ops)
│   ├── mongodb/                # Day-2: install, backup, restart, restore, ...
│   └── patroni/                # Day-2: switchover, health, rotate-passwords, ...
├── plugins/                    # Custom modules / filters / callbacks
└── roles/                      # All the capability; 12 first-party roles
```

## Contribution norms

**Commits.** One logical change per commit. Subject ≤ 70 chars; body explains
the *why*, not the *what* (the diff shows the what). Example:

```
roles/patroni: depend vip-manager on etcd, not patroni

vip-manager tracks the leader key in etcd; it doesn't need Patroni up on
the observing node. Previously we'd block vip-manager startup whenever
Patroni was slow to come up, even though the VIP lookup would have
succeeded via etcd directly.
```

**Changelog.** Every user-visible change gets a line in `CHANGELOG.md` under
`[Unreleased]`. Keep sections: Added / Changed / Removed / Fixed.

**Role defaults.** New vars go in `roles/<name>/defaults/main.yml`, typed in
`roles/<name>/meta/argument_specs.yml`, and mentioned in the role's README.

**Opt-in new capabilities.** Default behavior should never break existing
inventories. New features gate on a `<feature>_enabled: false` variable
(see `firewall_enabled`, `routeros_enabled`, `routeros_prune_stale`).

**Secrets.** Never commit plaintext secrets. Use the `vault_` prefix pattern
documented in [docs/SECRETS.md](docs/SECRETS.md).

**Documentation.** Role changes that affect inputs or outputs need a matching
update to that role's README and to `docs/ARCHITECTURE.md` if they change
the role topology.

## Release process (maintainers)

1. Merge all target PRs into `main`.
2. Move `CHANGELOG.md` entries from `[Unreleased]` into a new version block.
3. Bump `galaxy.yml: version`.
4. Commit and tag: `git tag v0.X.Y && git push --tags`.
5. `.github/workflows/release.yml` publishes the tarball to Ansible Galaxy.

## Filing a good issue

- **Repro steps** — which inventory? which playbook invocation? what flags?
- **Ansible version** — `ansible --version` output.
- **Collection version** — `ansible-galaxy collection list startechnica.infra`.
- **Relevant output** — the failing task + its error, not the whole log.

## Code of conduct

Be kind. Assume good faith. If a review comment lands as harsh, that's on
the reviewer — reply with what would help.
