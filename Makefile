PYTHON ?= python3
RESULTS_DIR ?= results

.DEFAULT_GOAL := help
.PHONY: help install lint format test check sync-configs build clean

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

check: ## Run the demo plan's checklist end to end
	$(PYTHON) -m harness check --plan tests/fixtures/demo-pipeline.yaml \
		--results-dir $(RESULTS_DIR)/check

sync-configs: ## Refresh the shipped agents.yaml header from setup.HEADER
	$(PYTHON) scripts/sync_configs.py

# `clean` first, always: setuptools reuses a stale build/lib without saying so,
# and a wheel built over one ships files that were deleted releases ago.
#
# The name check is not paranoia. An old pip cannot read this project's
# `[tool.setuptools] packages` + `package-dir`, does not say so, and writes an
# `UNKNOWN-0.0.0` wheel with nothing in it but metadata — a release that looks
# like it built.
build: clean ## Build the wheel, then list exactly what it would ship
	$(PYTHON) -m pip wheel . --no-deps -w dist -q
	@test -z "$$(ls dist | grep -i UNKNOWN)" || { \
		echo "build fell back: $$(ls dist). $(PYTHON) cannot read this pyproject —"; \
		echo "use a python with pip>=23, e.g. make build PYTHON=/path/to/python"; \
		exit 1; }
	@$(PYTHON) -c "import glob,zipfile;print(chr(10).join(sorted(zipfile.ZipFile(glob.glob('dist/*.whl')[0]).namelist())))"

clean: ## Remove generated artifacts
	rm -rf $(RESULTS_DIR) .pytest_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
