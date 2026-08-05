# Modularization proposal — reducing size & duplication

**Status:** proposal / for review
**Date:** 2026-08-06
**Scope:** `startechnica.infra` collection

## Motivation

The collection has grown large (~24k LOC across 13 roles, 41 playbooks, 4
modules). The felt "bigness" is **not** primarily too many responsibilities per
role — it's **the same logic copy-pasted across roles and across container
engines.** Extracting a role only pays off when it removes duplication or
decouples a genuinely reusable capability; splitting a coupled slice (e.g.
mongodb-backup) just adds an interface without deleting a line.

## Survey (as of 2026-08-06)

Role sizes by task LOC:

| Role | tasks LOC | task files | templates | Note |
|---|---|---|---|---|
| patroni | 3950 | 52 | 27 | docker/ + podman/ mirror (13 files each) |
| mongodb | 3160 | 32 | 16 | backup/pbm woven into core |
| instance | 2766 | 28 | 0 | large but cohesive (VM lifecycle) |
| preflight | 925 | 17 | 0 | |
| common | 779 | 8 | 0 | |
| netbox_register | 724 | 6 | 0 | |
| netbox_lookup | 513 | 5 | 0 | |
| instance_ignition | 484 | 10 | 4 | |
| grafana_alloy | 381 | 9 | 4 | |
| vcenter_sync | 380 | 5 | 0 | |
| firewall | 362 | 4 | 1 | |
| routeros | 202 | 5 | 0 | |
| instance_cloud_init | 199 | 7 | 2 | |

- **No `meta` dependencies** anywhere — roles are wired only through playbooks.
  Flexible, but shared logic can't currently be pulled in as a role dep cleanly.
- **Plugins:** `mongodb_backup`, `mongodb_rs_init`, `mongodb_status`,
  `mongodb_restore` (all mongodb-specific).

## Findings — where the duplication actually is

| Concern | Evidence | Verdict |
|---|---|---|
| `instance` role | 2766 LOC, 28 files, subdirs `create_vm/`, `validate/` | Big but **cohesive**, one job. Not the problem. |
| patroni `docker/` vs `podman/` mirror | 13 files each; `rotate_passwords` ~84% identical, `walg`/`preflight`/`switchover`/`_detect_leader` ~75%+ identical | **Real duplication** (~1500 LOC largely copy-paste). |
| PKI/CA generation | Duplicated in `mongodb` (4 files) *and* `patroni` (`tls_certificates.yml` 363 LOC + renew ×2) | **Real duplication** — both self-sign a per-cluster CA the same way. |
| S3 upload | Logic in `common`, `mongodb`, `patroni` templates | **Partial** — but patroni uses WAL-G (native S3), mongodb uses containerized `mc`; thin real overlap. |

Engine-mirror overlap (patroni `docker/` vs `podman/`, changed lines out of total):

| File | changed / total | Extract candidate? |
|---|---|---|
| rotate_passwords.yml | 12 / 74 | ✅ easy |
| walg.yml | 18 / 12 | ✅ easy |
| preflight.yml | 18 / 37 | ✅ easy |
| switchover.yml | 18 / 65 | ✅ easy |
| _detect_leader.yml | 19 / 82 | ✅ easy |
| status.yml | 39 / 126 | ⚠️ moderate |
| health.yml | 47 / 74 | ⚠️ moderate |
| renew_certs.yml | 42 / 59 | ⚠️ moderate |
| deploy.yml | 230 / 52 | ❌ genuinely diverges (compose vs quadlet) |
| uninstall.yml | 112 / 41 | ❌ genuinely diverges |

## Recommendations — ranked by payoff

### 1. Collapse the `docker/` vs `podman/` mirror (highest payoff)

Single biggest win. In patroni, 13 files are maintained twice; many are
near-identical. Every bugfix is a double-edit (hit exactly this in the standby
work — `_detect_leader.yml` had to be edited in both engine dirs).

- **Not a role split** — make it a variable-driven single path. Where the two
  differ only in the binary (`docker` vs `podman`) plus compose-vs-quadlet, that's
  `{{ patroni_container_engine }}` + a few `when`s, not two files.
- **Easy wins first:** `rotate_passwords`, `walg`, `preflight`, `switchover`,
  `_detect_leader` (75–85% identical).
- **Leave split:** `deploy.yml` / `uninstall.yml` — they genuinely diverge
  (compose file vs quadlet units).
- **Payoff:** ~600–800 LOC deleted, no new interface, regression surface limited
  to files touched.

### 2. Extract a `pki` (CA/cert) role — the one true role-split

Both `mongodb` and `patroni` self-sign a per-cluster CA and mint member/client
certs with `community.crypto`, on localhost, then push to nodes. This is a clean,
reusable capability with a real contract (in: subject vars, key type, days; out:
certs in a dir) and **no runtime coupling** — pure controller-side crypto.

- This is the split worth endorsing: `startechnica.infra.pki`, called by both.
- Makes the new `patroni_shared_ca_dir` concept natural — shared-CA becomes
  "point two clusters at one pki invocation."
- **Caveat:** subject/OU conventions differ (`MongoDBCluster` OU vs patroni's),
  so the contract must be parameterized, not hardcoded.
- **Effort:** medium. **Payoff:** high clarity, removes real duplication.

### 3. mongodb `tasks/` reorg into `backup/` + `pbm/` subdirs (cosmetic)

Zero-risk file organization, no variable-passing contract. Do it if the file list
feels cluttered; skip if not.

### 4. `s3_object` upload helper — NOT yet

The containerized-`mc` upload/verify pattern recurs, but patroni uses WAL-G
(native S3) and mongodb uses `mc` — the actual shared surface is thin. Extracting
now would force the abstraction. Revisit only if a third consumer appears.

## Explicitly do NOT

- **Don't split mongodb-backup or pbm into roles** — coupled to auth, PKI,
  runtime, and cluster-init ordering (PBM per-RS user is created during
  `init_cluster.yml`; PBM setup mints an X.509 cert from the parent CA). A split
  would need ~20 vars passed in + shared certs dir — an interface with no payoff.
  Subdirs at most (see #3).
- **Don't split `instance`** — large but single-purpose (VM lifecycle).
- **Don't add a "shared" catch-all role** — becomes its own dumping-ground problem.

## Suggested sequencing

Each is independently shippable and testable:

1. **De-duplicate patroni's engine mirror** — daily pain, biggest LOC win, lowest
   risk. Start with the 5 easy-win files.
2. **Extract a `pki` role** — the one genuinely reusable, cleanly-decoupled
   capability. Parameterize the subject/OU contract.
3. **mongodb `tasks/` subdir reorg** — cosmetic, zero-risk.

Defer #4 (s3 helper) indefinitely unless a third consumer lands.
