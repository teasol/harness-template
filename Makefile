PYTHON ?= python3
RESULTS_DIR ?= results

# Point these at your project's own spec/plan. `make drift` needs neither:
# it checks every plan in plans/.
SPEC ?= configs/demo.yaml
PLAN ?= plans/demo-pipeline.yaml

.DEFAULT_GOAL := help
.PHONY: help status setup worker-setup lint format test verify plan tasks run reproduce drift audit experiments clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

status: ## Where am I and what do I do next (start here)
	@$(PYTHON) -m harness status

setup: ## Editable install + dev tools
	$(PYTHON) -m pip install -e ".[dev]"

worker-setup: ## Choose the Worker platform / model / reasoning level
	$(PYTHON) -m harness setup

lint: ## Run ruff checks (lint + format check)
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format: ## Auto-format code with ruff
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

test: ## Run the pytest suite
	$(PYTHON) -m pytest

verify: ## Run the verification harness (configs/demo.yaml)
	$(PYTHON) -m harness verify --spec $(SPEC) --results-dir $(RESULTS_DIR)

plan: ## Validate the orchestration plan and refresh task files
	$(PYTHON) -m harness plan validate $(PLAN)
	$(PYTHON) -m harness plan materialize $(PLAN)
	$(PYTHON) -m harness plan status $(PLAN)

tasks: ## Show the worker task board
	$(PYTHON) -m harness task list

reproduce: ## Run the spec twice and compare artifact hashes (determinism gate)
	$(PYTHON) -m harness reproduce --spec $(SPEC) --times 2 \
		--results-dir $(RESULTS_DIR)/reproduce

drift: ## Validate every plan and fail on task/plan drift
	$(PYTHON) -m harness plan check

audit: ## Re-verify every task marked done (acceptance + deliverables)
	$(PYTHON) -m harness task verify --all --status done --results-dir $(RESULTS_DIR)/audit

run: ## Run every ready task through a Worker (see configs/worker.yaml)
	$(PYTHON) -m harness plan run $(PLAN)

experiments: ## List experiment branches/worktrees
	$(PYTHON) -m harness exp list

clean: ## Remove generated artifacts
	rm -rf $(RESULTS_DIR) .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
