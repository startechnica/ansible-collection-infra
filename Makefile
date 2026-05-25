# ════════════════════════════════════════════════════════════════════════════
# Wrapper for common Ansible operations — site-repo style.
# Usage:  make <target> INV=<inventory-name> [extra=...]
#
#   make help                     show all targets
#   make deploy   INV=<name>      run full deploy playbook
#   make status   INV=<name>      patroni cluster status
#   make backup   INV=<name>      on-demand patroni backup
#   make destroy  INV=<name>      destroy VMs (interactive prompt)
#   make sync     INV=<name>      sync vCenter -> NetBox
#
# INV resolution:
#   INV=foo               -> inventories/foo.yml
#   INV=foo.yml           -> inventories/foo.yml
#   INV=path/to/foo.yml   -> path/to/foo.yml          (used as-is)
#
# All targets accept extra="<ansible-playbook flags>" for one-off overrides:
#   make deploy INV=<name> extra="--check --diff --tags patroni"
#
# Designed so the same Makefile works whether inventories live in this repo
# (current layout) or in a separate site repo (future split). Override
# PLAYBOOK_DIR / INV_DIR via env or .envrc if the layout differs.
# ════════════════════════════════════════════════════════════════════════════

PLAYBOOK_DIR ?= playbooks
INV_DIR      ?= inventories
INV          ?=
extra        ?=

# Resolve INV=manu -> inventories/manu.yml; absolute paths and explicit .yml
# both pass through unchanged so `make deploy INV=/tmp/foo.yml` also works.
ifeq ($(INV),)
INVENTORY :=
else ifneq (,$(findstring /,$(INV)))
INVENTORY := $(INV)
else ifneq (,$(findstring .yml,$(INV)))
INVENTORY := $(INV_DIR)/$(INV)
else
INVENTORY := $(INV_DIR)/$(INV).yml
endif

ANSIBLE := ansible-playbook
PLAY    := $(ANSIBLE) -i $(INVENTORY)

# Coloured help. Self-documenting via "## " annotations in the target line.
.DEFAULT_GOAL := help

.PHONY: help check-inv

help:  ## Show this help
	@echo "Usage: make <target> INV=<inventory> [extra='<flags>']"
	@echo ""
	@echo "Targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Examples:"
	@echo "  make deploy INV=<name>"
	@echo "  make backup INV=<name> extra='--limit <hostname>'"
	@echo "  make switchover INV=<name> target=<hostname>"
	@echo ""
	@echo "Available inventories:"
	@ls $(INV_DIR)/*.yml 2>/dev/null | sed 's|$(INV_DIR)/|  - |;s|\.yml$$||' || echo "  (none found in $(INV_DIR)/)"

check-inv:
	@if [ -z "$(INV)" ]; then \
	  echo "ERROR: INV=<name> is required (e.g. make deploy INV=<inventory>)"; \
	  exit 2; \
	fi
	@if [ ! -f $(INVENTORY) ]; then \
	  echo "ERROR: inventory not found: $(INVENTORY)"; \
	  exit 2; \
	fi

# ─── Full-stack deploys ────────────────────────────────────────────────────

.PHONY: deploy deploy-patroni deploy-mongodb

deploy: check-inv  ## Full deploy (everything in inventory)
	$(PLAY) $(PLAYBOOK_DIR)/deploy.yml $(extra)

deploy-patroni: check-inv  ## Full Patroni stack (VMs + cluster)
	$(PLAY) $(PLAYBOOK_DIR)/deploy_patroni.yml $(extra)

deploy-mongodb: check-inv  ## Full MongoDB stack (VMs + cluster)
	$(PLAY) $(PLAYBOOK_DIR)/deploy_mongodb.yml $(extra)

# ─── Patroni day-2 actions ─────────────────────────────────────────────────
# Each maps to patroni_action=<x>. Use deploy_patroni playbook with --tags
# patroni so the VM-provisioning stages are skipped.

.PHONY: status backup health switchover renew-certs rotate-passwords manage-databases uninstall

status: check-inv  ## Patroni cluster status
	$(PLAY) $(PLAYBOOK_DIR)/deploy_patroni.yml --tags patroni \
	  -e patroni_action=status $(extra)

backup: check-inv  ## On-demand Patroni backup (WAL-G or pg_basebackup)
	$(PLAY) $(PLAYBOOK_DIR)/deploy_patroni.yml --tags patroni \
	  -e patroni_action=backup $(extra)

health: check-inv  ## Patroni health checks across all nodes
	$(PLAY) $(PLAYBOOK_DIR)/deploy_patroni.yml --tags patroni \
	  -e patroni_action=health $(extra)

switchover: check-inv  ## Patroni switchover (set target=<hostname>)
	@if [ -z "$(target)" ]; then \
	  echo "ERROR: target=<hostname> is required for switchover"; exit 2; \
	fi
	$(PLAY) $(PLAYBOOK_DIR)/deploy_patroni.yml --tags patroni \
	  -e patroni_action=switchover -e target_node=$(target) $(extra)

renew-certs: check-inv  ## Regenerate TLS certs (add rotate=true for CA)
	$(PLAY) $(PLAYBOOK_DIR)/deploy_patroni.yml --tags patroni \
	  -e patroni_action=renew-certs $(extra)

rotate-passwords: check-inv  ## Rotate PostgreSQL + PgBouncer passwords (run with --forks=1)
	$(PLAY) $(PLAYBOOK_DIR)/deploy_patroni.yml --tags patroni \
	  -e patroni_action=rotate-passwords --forks 1 $(extra)

manage-databases: check-inv  ## Re-apply patroni_databases (DBs/users/grants)
	$(PLAY) $(PLAYBOOK_DIR)/deploy_patroni.yml --tags patroni \
	  -e patroni_action=manage-databases $(extra)

uninstall: check-inv  ## Tear down Patroni stack (data preserved unless prune=true)
	$(PLAY) $(PLAYBOOK_DIR)/deploy_patroni.yml --tags patroni \
	  -e patroni_action=uninstall $(extra)

# ─── MongoDB day-2 actions ─────────────────────────────────────────────────

.PHONY: mongo-status mongo-backup mongo-uninstall

mongo-status: check-inv  ## MongoDB cluster status
	$(PLAY) $(PLAYBOOK_DIR)/deploy_mongodb.yml --tags mongodb \
	  -e mongodb_action=status $(extra)

mongo-backup: check-inv  ## On-demand MongoDB backup
	$(PLAY) $(PLAYBOOK_DIR)/deploy_mongodb.yml --tags mongodb \
	  -e mongodb_action=backup $(extra)

mongo-uninstall: check-inv  ## Tear down MongoDB stack
	$(PLAY) $(PLAYBOOK_DIR)/deploy_mongodb.yml --tags mongodb \
	  -e mongodb_action=uninstall $(extra)

# ─── Infra utilities ──────────────────────────────────────────────────────

.PHONY: sync destroy validate dry-run lint

sync: check-inv  ## Sync vCenter inventory into NetBox
	$(PLAY) $(PLAYBOOK_DIR)/sync_vcenter_netbox.yml $(extra)

destroy: check-inv  ## Destroy VMs (interactive — pass extra='-e instance_destroy_skip_confirm=true' for CI)
	$(PLAY) $(PLAYBOOK_DIR)/destroy.yml $(extra)

validate: check-inv  ## Validate inventory + roles WITHOUT touching anything
	$(PLAY) $(PLAYBOOK_DIR)/deploy.yml --syntax-check && \
	$(PLAY) $(PLAYBOOK_DIR)/deploy.yml --check --diff -e validation_only=true $(extra)

dry-run: check-inv  ## Full deploy in --check --diff mode
	$(PLAY) $(PLAYBOOK_DIR)/deploy.yml --check --diff $(extra)

lint:  ## Run ansible-lint over the whole tree (no inventory needed)
	ansible-lint roles/ playbooks/

# ─── Inventory introspection ──────────────────────────────────────────────

.PHONY: list-hosts graph

list-hosts: check-inv  ## Show all hosts the inventory resolves to
	ansible-inventory -i $(INVENTORY) --list

graph: check-inv  ## Pretty-print the inventory tree
	ansible-inventory -i $(INVENTORY) --graph
