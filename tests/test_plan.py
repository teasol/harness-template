"""Tests for orchestration plan loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.plan import PlanError, load_plan


def write_plan(tmp_path: Path, data: str) -> Path:
    path = tmp_path / "plan.yaml"
    path.write_text(data, encoding="utf-8")
    return path


VALID_PLAN = """
plan:
  name: p
  goal: do something
  modules:
    - id: a
      brief: build a
      acceptance:
        steps:
          - id: run-a
            run: "true"
            checks:
              - type: file_exists
                path: out.txt
    - id: b
      brief: build b
      depends_on: [a]
      acceptance:
        steps:
          - id: run-b
            run: "true"
"""


def test_load_valid_plan(tmp_path: Path) -> None:
    plan = load_plan(write_plan(tmp_path, VALID_PLAN))
    assert plan.name == "p"
    assert [m.id for m in plan.modules] == ["a", "b"]
    assert plan.topological_order() == ["a", "b"]
    assert plan.modules[1].depends_on == ["a"]
    assert plan.modules[0].contract.outputs == []  # contract is optional


def test_topological_order(tmp_path: Path) -> None:
    plan = load_plan(write_plan(tmp_path, VALID_PLAN))
    assert plan.topological_order() == ["a", "b"]
    assert plan.module("b").title == "b"  # defaults to id


def test_cycle_detected(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
plan:
  name: p
  goal: g
  modules:
    - id: a
      brief: b
      depends_on: [c]
      acceptance: {steps: [{id: s, run: "true"}]}
    - id: b
      brief: b
      depends_on: [a]
      acceptance: {steps: [{id: s, run: "true"}]}
    - id: c
      brief: b
      depends_on: [b]
      acceptance: {steps: [{id: s, run: "true"}]}
""",
    )
    with pytest.raises(PlanError, match="cycle"):
        load_plan(path)


def test_unknown_dependency(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
plan:
  name: p
  goal: g
  modules:
    - id: a
      brief: b
      depends_on: [ghost]
      acceptance: {steps: [{id: s, run: "true"}]}
""",
    )
    with pytest.raises(PlanError, match="unknown module"):
        load_plan(path)


def test_duplicate_module_ids(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
plan:
  name: p
  goal: g
  modules:
    - id: a
      brief: b
      acceptance: {steps: [{id: s, run: "true"}]}
    - id: a
      brief: b
      acceptance: {steps: [{id: s, run: "true"}]}
""",
    )
    with pytest.raises(PlanError, match="duplicate"):
        load_plan(path)


def test_missing_brief_rejected(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
plan:
  name: p
  goal: g
  modules:
    - id: a
      acceptance: {steps: [{id: s, run: "true"}]}
""",
    )
    with pytest.raises(PlanError, match="brief"):
        load_plan(path)


def test_missing_acceptance_rejected(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
plan:
  name: p
  goal: g
  modules:
    - id: a
      brief: b
""",
    )
    with pytest.raises(PlanError, match="acceptance"):
        load_plan(path)


def test_unknown_check_type_rejected(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
plan:
  name: p
  goal: g
  modules:
    - id: a
      brief: b
      acceptance:
        steps:
          - id: s
            run: "true"
            checks: [{type: nope, path: x}]
""",
    )
    with pytest.raises(PlanError, match="unknown check type"):
        load_plan(path)


def test_integration_spec_must_exist(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
plan:
  name: p
  goal: g
  integration: {spec: configs/missing.yaml}
  modules:
    - id: a
      brief: b
      acceptance: {steps: [{id: s, run: "true"}]}
""",
    )
    with pytest.raises(PlanError, match="integration spec not found"):
        load_plan(path)


def test_goal_required(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
plan:
  name: p
  modules:
    - id: a
      brief: b
      acceptance: {steps: [{id: s, run: "true"}]}
""",
    )
    with pytest.raises(PlanError, match="goal"):
        load_plan(path)


def test_missing_file_raises() -> None:
    with pytest.raises(PlanError, match="not found"):
        load_plan("/nonexistent/plan.yaml")
