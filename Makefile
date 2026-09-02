PYTHON ?= python3
RESULTS_DIR ?= results

# This package's own end-to-end spec. It is a test fixture, not a project's
# configuration: this repository does not run the harness on itself, so the
# targets that operate on a plan or a task board do not belong here.
SPEC ?= tests/fixtures/configs/demo.yaml

.DEFAULT_GOAL := help
.PHONY: help install lint format test verify reproduce sync-configs build clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Editable install with the dev tools
	$(PYTHON) -m pip install -e ".[dev]"

lint: ## Run ruff checks (lint + format check)
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format: ## Auto-format code with ruff
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

test: ## Run the pytest suite
	$(PYTHON) -m pytest

verify: ## Run the verification engine end to end on this package's own spec
	$(PYTHON) -m harness verify --spec $(SPEC) --results-dir $(RESULTS_DIR)

reproduce: ## Run that spec twice and compare artifact hashes (determinism gate)
	$(PYTHON) -m harness reproduce --spec $(SPEC) --times 2 \
		--results-dir $(RESULTS_DIR)/reproduce

sync-configs: ## Refresh the shipped agents.yaml header from setup.HEADER
	$(PYTHON) scripts/sync_configs.py

# `clean` first, always: setuptools reuses a stale build/lib without saying so,
# and a wheel built over one ships files that were deleted releases ago.
build: clean ## Build the wheel, then list exactly what it would ship
	$(PYTHON) -m pip wheel . --no-deps -w dist -q
	@$(PYTHON) -c "import glob,zipfile;print(chr(10).join(sorted(zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist())))"

clean: ## Remove generated artifacts
	rm -rf $(RESULTS_DIR) .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
