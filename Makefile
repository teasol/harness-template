PYTHON ?= python
RESULTS_DIR ?= results

.DEFAULT_GOAL := help
.PHONY: help setup lint format test verify reproduce clean

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

reproduce: ## Re-run verification into a fresh results subdir (determinism check)
	$(PYTHON) -m harness verify --spec configs/demo.yaml --results-dir $(RESULTS_DIR)/reproduce

clean: ## Remove generated artifacts
	rm -rf $(RESULTS_DIR) .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
