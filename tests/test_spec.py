"""Tests for spec loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.spec import SpecError, load_spec


def write_spec(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_load_valid_spec(tmp_path: Path) -> None:
    path = write_spec(
        tmp_path,
        {
            "name": "my-spec",
            "description": "test spec",
            "seed": 7,
            "steps": [
                {
                    "id": "step-one",
                    "run": "echo hello",
                    "timeout": 10,
                    "env": {"FOO": "bar"},
                    "checks": [{"type": "file_exists", "path": "out.txt"}],
                }
            ],
        },
    )
    spec = load_spec(path)
    assert spec.name == "my-spec"
    assert spec.seed == 7
    assert len(spec.steps) == 1
    step = spec.steps[0]
    assert step.id == "step-one"
    assert step.run == "echo hello"
    assert step.timeout == 10
    assert step.env == {"FOO": "bar"}
    assert step.checks[0].type == "file_exists"
    assert step.checks[0].params == {"path": "out.txt"}


def test_missing_file_raises() -> None:
    with pytest.raises(SpecError, match="not found"):
        load_spec("/nonexistent/spec.yaml")


def test_step_requires_id_and_run(tmp_path: Path) -> None:
    path = write_spec(tmp_path, {"name": "bad", "steps": [{"id": "a"}]})
    with pytest.raises(SpecError, match="requires 'id' and 'run'"):
        load_spec(path)


def test_check_requires_type(tmp_path: Path) -> None:
    path = write_spec(
        tmp_path,
        {"name": "bad", "steps": [{"id": "a", "run": "true", "checks": [{"path": "x"}]}]},
    )
    with pytest.raises(SpecError, match="'type'"):
        load_spec(path)


def test_duplicate_step_ids(tmp_path: Path) -> None:
    path = write_spec(
        tmp_path,
        {
            "name": "dup",
            "steps": [
                {"id": "a", "run": "true"},
                {"id": "a", "run": "true"},
            ],
        },
    )
    with pytest.raises(SpecError, match="duplicate"):
        load_spec(path)


def test_non_mapping_root(tmp_path: Path) -> None:
    path = tmp_path / "spec.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(SpecError, match="root must be a mapping"):
        load_spec(path)


def test_defaults_from_empty_spec(tmp_path: Path) -> None:
    path = write_spec(tmp_path, {})
    spec = load_spec(path)
    assert spec.name == "spec"  # falls back to the file stem
    assert spec.steps == []
    assert spec.seed is None
