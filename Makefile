PYTHON ?= python3
RESULTS_DIR ?= results

.DEFAULT_GOAL := help
.PHONY: help setup lint format test verify plan tasks reproduce drift audit clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Editable install + dev tools
	$(PYTHON) -m pip install -e ".[dev]"

lint: ## Run ruff checks (lint + format check)
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format: ## Auto-format code with ruff
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

test: ## Run the pytest suite
	$(PYTHON) -m pytest

verify: ## Run the verification harness (configs/demo.yaml)
	$(PYTHON) -m harness verify --spec configs/demo.yaml --results-dir $(RESULTS_DIR)

plan: ## Validate the orchestration plan and refresh task files
	$(PYTHON) -m harness plan validate plans/demo-pipeline.yaml
	$(PYTHON) -m harness plan materialize plans/demo-pipeline.yaml
	$(PYTHON) -m harness plan status plans/demo-pipeline.yaml

tasks: ## Show the worker task board
	$(PYTHON) -m harness task list

reproduce: ## Run the spec twice and compare artifact hashes (determinism gate)
	$(PYTHON) -m harness reproduce --spec configs/demo.yaml --times 2 \
		--results-dir $(RESULTS_DIR)/reproduce

drift: ## Fail if task files have drifted from the plan
	$(PYTHON) -m harness plan status plans/demo-pipeline.yaml --check

audit: ## Re-verify every task marked done (acceptance + deliverables)
	$(PYTHON) -m harness task verify --all --status done --results-dir $(RESULTS_DIR)/audit

clean: ## Remove generated artifacts
	rm -rf $(RESULTS_DIR) .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
