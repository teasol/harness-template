"""Tests for built-in checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.checks import CheckError, run_check
from harness.reproducibility import file_sha256


def test_file_exists(check_root: Path) -> None:
    target = check_root / "out.txt"
    target.write_text("hello", encoding="utf-8")
    detail = run_check("file_exists", {"path": "out.txt"}, check_root)
    assert "out.txt" in detail


def test_file_exists_missing(check_root: Path) -> None:
    with pytest.raises(CheckError, match="not found"):
        run_check("file_exists", {"path": "missing.txt"}, check_root)


def test_file_exists_requires_path(check_root: Path) -> None:
    with pytest.raises(CheckError, match="requires a 'path'"):
        run_check("file_exists", {}, check_root)


def test_file_hash(check_root: Path) -> None:
    target = check_root / "out.bin"
    target.write_bytes(b"deterministic bytes")
    digest = file_sha256(target)
    run_check("file_hash", {"path": "out.bin", "sha256": digest}, check_root)


def test_file_hash_mismatch(check_root: Path) -> None:
    (check_root / "out.bin").write_bytes(b"other bytes")
    with pytest.raises(CheckError, match="mismatch"):
        run_check("file_hash", {"path": "out.bin", "sha256": "0" * 64}, check_root)


def test_json_metric_bounds(check_root: Path) -> None:
    payload = {"metrics": {"accuracy": 0.91}, "n": 10}
    (check_root / "metrics.json").write_text(json.dumps(payload), encoding="utf-8")
    run_check(
        "json_metric",
        {"path": "metrics.json", "metric": "metrics.accuracy", "min": 0.5, "max": 1.0},
        check_root,
    )
    run_check("json_metric", {"path": "metrics.json", "metric": "n", "equals": 10}, check_root)


def test_json_metric_below_min(check_root: Path) -> None:
    (check_root / "metrics.json").write_text(json.dumps({"x": 0.1}), encoding="utf-8")
    with pytest.raises(CheckError, match="below min"):
        run_check("json_metric", {"path": "metrics.json", "metric": "x", "min": 0.5}, check_root)


def test_json_metric_missing_metric(check_root: Path) -> None:
    (check_root / "metrics.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
    with pytest.raises(CheckError, match="not found"):
        run_check("json_metric", {"path": "metrics.json", "metric": "y"}, check_root)


def test_json_metric_non_numeric(check_root: Path) -> None:
    (check_root / "metrics.json").write_text(json.dumps({"x": "abc"}), encoding="utf-8")
    with pytest.raises(CheckError, match="not numeric"):
        run_check("json_metric", {"path": "metrics.json", "metric": "x"}, check_root)


def test_text_contains(check_root: Path) -> None:
    (check_root / "log.txt").write_text("epoch 3 loss 0.42 ok\n", encoding="utf-8")
    run_check("text_contains", {"path": "log.txt", "contains": ["epoch 3", "ok"]}, check_root)


def test_text_contains_missing(check_root: Path) -> None:
    (check_root / "log.txt").write_text("nothing here\n", encoding="utf-8")
    with pytest.raises(CheckError, match="missing"):
        run_check("text_contains", {"path": "log.txt", "contains": "loss"}, check_root)


def test_unknown_check_type(check_root: Path) -> None:
    with pytest.raises(CheckError, match="unknown check type"):
        run_check("nope", {}, check_root)


def test_env_var_expansion(check_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TEST_DIR", str(check_root))
    target = check_root / "out.txt"
    target.write_text("hi", encoding="utf-8")
    run_check("file_exists", {"path": "${MY_TEST_DIR}/out.txt"}, check_root)
