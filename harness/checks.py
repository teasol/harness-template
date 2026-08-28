"""Built-in verification checks.

Each check is a function ``(root: Path, params: dict) -> detail: str``. A check
passes by returning normally and fails by raising :class:`CheckError`. Path
params are resolved relative to the run root and support ``${VAR}`` environment
variable expansion (e.g. ``${HARNESS_RESULTS_DIR}``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harness.reproducibility import file_sha256


class CheckError(Exception):
    """Raised when a check fails."""


def _resolve(root: Path, path: str) -> Path:
    expanded = os.path.expandvars(str(path))
    resolved = Path(expanded)
    return resolved if resolved.is_absolute() else (root / resolved)


def _require(params: dict[str, Any], key: str, check_type: str) -> Any:
    if key not in params:
        raise CheckError(f"{check_type} requires a '{key}' param")
    return params[key]


def check_file_exists(root: Path, params: dict[str, Any]) -> str:
    path = _require(params, "path", "file_exists")
    resolved = _resolve(root, path)
    if not resolved.is_file():
        raise CheckError(f"file not found: {resolved}")
    return f"file exists: {resolved}"


def check_file_hash(root: Path, params: dict[str, Any]) -> str:
    path = _require(params, "path", "file_hash")
    expected = _require(params, "sha256", "file_hash")
    resolved = _resolve(root, path)
    if not resolved.is_file():
        raise CheckError(f"file not found: {resolved}")
    actual = file_sha256(resolved)
    if actual.lower() != str(expected).lower():
        raise CheckError(f"sha256 mismatch for {resolved}: expected {expected}, got {actual}")
    return f"sha256 ok: {actual}"


def _lookup_metric(data: Any, metric: str) -> Any:
    node: Any = data
    for part in metric.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise CheckError(f"metric path '{metric}' not found in JSON document")
    return node


def check_json_metric(root: Path, params: dict[str, Any]) -> str:
    path = _require(params, "path", "json_metric")
    metric = _require(params, "metric", "json_metric")
    resolved = _resolve(root, path)
    if not resolved.is_file():
        raise CheckError(f"file not found: {resolved}")
    try:
        with open(resolved, encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise CheckError(f"invalid JSON in {resolved}: {exc}") from exc

    value = _lookup_metric(data, metric)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CheckError(f"metric '{metric}' is not numeric: {value!r}")

    if "equals" in params and value != params["equals"]:
        raise CheckError(f"{metric}={value}, expected equals {params['equals']}")
    if "min" in params and value < params["min"]:
        raise CheckError(f"{metric}={value}, below min {params['min']}")
    if "max" in params and value > params["max"]:
        raise CheckError(f"{metric}={value}, above max {params['max']}")
    return f"{metric}={value} within bounds"


def check_text_contains(root: Path, params: dict[str, Any]) -> str:
    path = _require(params, "path", "text_contains")
    needles = _require(params, "contains", "text_contains")
    if isinstance(needles, str):
        needles = [needles]
    resolved = _resolve(root, path)
    if not resolved.is_file():
        raise CheckError(f"file not found: {resolved}")
    text = resolved.read_text(encoding="utf-8", errors="replace")
    missing = [n for n in needles if n not in text]
    if missing:
        raise CheckError(f"missing {len(missing)} substring(s) in {resolved}: {missing}")
    return f"contains {len(needles)} substring(s)"


CHECK_REGISTRY: dict[str, Callable[[Path, dict[str, Any]], str]] = {
    "file_exists": check_file_exists,
    "file_hash": check_file_hash,
    "json_metric": check_json_metric,
    "text_contains": check_text_contains,
}


def run_check(check_type: str, params: dict[str, Any], root: Path) -> str:
    """Execute a single check by type name. Raises CheckError on failure."""
    func = CHECK_REGISTRY.get(check_type)
    if func is None:
        raise CheckError(f"unknown check type '{check_type}'. available: {sorted(CHECK_REGISTRY)}")
    return func(root, params)
