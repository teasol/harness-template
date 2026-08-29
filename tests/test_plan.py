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


# ---------------------------------------------------------------------------
# approval — a plan is a proposal until someone agrees to it


def test_a_fresh_plan_is_not_approved(tmp_path: Path) -> None:
    from harness.plan import approval_status

    path = write_plan(tmp_path, VALID_PLAN)
    approved, reason = approval_status(path)
    assert not approved
    assert "never been approved" in reason


def test_approval_is_tied_to_the_plans_contents(tmp_path: Path) -> None:
    """Approving a plan must not approve whatever it is edited into next."""
    from harness.plan import approval_status, record_approval

    path = write_plan(tmp_path, VALID_PLAN)
    record_approval(path, by="researcher", note="looks right")
    approved, reason = approval_status(path)
    assert approved
    assert "researcher" in reason

    path.write_text(VALID_PLAN + "\n# a module snuck in later\n", encoding="utf-8")
    approved, reason = approval_status(path)
    assert not approved
    assert "changed after it was approved" in reason


def test_cost_estimate_counts_only_worker_modules(tmp_path: Path) -> None:
    """The Planner's own modules do not consume the agent budget."""
    from harness.plan import estimate_cost

    plan = load_plan(write_plan(tmp_path, VALID_PLAN))
    plan.modules[0].executor = "planner"
    cost = estimate_cost(plan, attempts=6, timeout=1800)
    assert cost["planner_modules"] == 1
    assert cost["worst_case_s"] == cost["worker_modules"] * 6 * 1800


def test_plan_run_refuses_without_approval(tmp_path: Path, capsys) -> None:
    """The gate sits where money starts being spent, not at validation."""
    from harness.cli import main
    from harness.plan import record_approval
    from harness.task import materialize

    path = write_plan(tmp_path, VALID_PLAN)
    plan = load_plan(path)
    materialize(plan, tmp_path / "tasks")

    rc = main(
        ["plan", "run", str(path), "--tasks-dir", str(tmp_path / "tasks"), "--root", str(tmp_path)]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "never been approved" in err
    assert "plan approve" in err
    # And it says what approving would commit you to.
    assert "of agent time" in err

    record_approval(path, by="researcher")
    rc = main(
        ["plan", "run", str(path), "--tasks-dir", str(tmp_path / "tasks"), "--root", str(tmp_path)]
    )
    assert "approved by researcher" in capsys.readouterr().out
