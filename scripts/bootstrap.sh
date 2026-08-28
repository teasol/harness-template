#!/usr/bin/env bash
# One-shot environment bootstrap: creates .venv and installs the project.
# Usage: bash scripts/bootstrap.sh   (override with PYTHON=... ENV_DIR=...)
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
ENV_DIR="${ENV_DIR:-.venv}"

"$PYTHON" -m venv "$ENV_DIR"
# shellcheck disable=SC1091
source "$ENV_DIR/bin/activate"

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

echo
echo "Done. Activate the environment with: source $ENV_DIR/bin/activate"
echo "Then run: make verify"
