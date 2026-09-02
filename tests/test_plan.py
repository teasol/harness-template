"""Tests for orchestration plan loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.plan import PlanError, load_plan


def write_plan(tmp_path: Path, data: str) -> Path:
    """Write a plan fixture, plus the integration spec every plan must declare.

    ``plan.integration.spec`` is required, so each fixture gets a trivial
    companion spec at ``configs/p.yaml``, and the key is added when the fixture
    did not write one. Tests about integration itself pass their own value.
    """
    path = tmp_path / "plan.yaml"
    path.write_text(data, encoding="utf-8")
    spec_dir = tmp_path / "configs"
    spec_dir.mkdir(exist_ok=True)
    (spec_dir / "p.yaml").write_text(
        "name: p\nsteps:\n  - id: ok\n    run: 'true'\n", encoding="utf-8"
    )
    raw = yaml.safe_load(data)
    if "integration" not in (raw or {}).get("plan", {}):
        raw["plan"]["integration"] = {"spec": "configs/p.yaml"}
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
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


def test_integration_spec_required(tmp_path: Path) -> None:
    """A plan without an integration spec is not a plan."""
    path = tmp_path / "plan.yaml"
    path.write_text(
        """
plan:
  name: p
  goal: g
  modules:
    - id: a
      brief: b
      acceptance: {steps: [{id: s, run: "true"}]}
""",
        encoding="utf-8",
    )
    with pytest.raises(PlanError, match="integration"):
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
    record_approval(path, by="user", note="looks right")
    approved, reason = approval_status(path)
    assert approved
    assert "user" in reason

    path.write_text(VALID_PLAN + "\n# a module snuck in later\n", encoding="utf-8")
    approved, reason = approval_status(path)
    assert not approved
    assert "changed after it was approved" in reason


def test_cost_estimate_counts_only_delegated_modules(tmp_path: Path) -> None:
    """What the Main Worker keeps does not consume the Sub-Worker budget."""
    from harness.plan import estimate_cost

    plan = load_plan(write_plan(tmp_path, VALID_PLAN))
    # Both modules default to the Main Worker, so an unmodified plan costs no
    # Sub-Worker time at all.
    assert estimate_cost(plan, attempts=6, timeout=1800)["worst_case_s"] == 0

    plan.modules[1].executor = "sub"
    cost = estimate_cost(plan, attempts=6, timeout=1800)
    assert cost["planner_modules"] == 1
    assert cost["worker_modules"] == 1
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

    record_approval(path, by="user")
    rc = main(
        ["plan", "run", str(path), "--tasks-dir", str(tmp_path / "tasks"), "--root", str(tmp_path)]
    )
    assert "approved by user" in capsys.readouterr().out


def test_executor_accepts_the_older_names(tmp_path: Path) -> None:
    """`planner`/`worker` were the same two roles under their previous names.

    Plans and task files written before the two-tier model must keep loading —
    the Planner *is* the Main Worker, so the rename says nothing new about them.
    """
    from harness.plan import normalize_executor

    assert normalize_executor("planner") == "main"
    assert normalize_executor("self") == "main"
    assert normalize_executor("MAIN") == "main"
    assert normalize_executor("worker") == "sub"
    assert normalize_executor("delegate") == "sub"
    assert normalize_executor("nonsense") is None

    plan = load_plan(
        write_plan(
            tmp_path,
            VALID_PLAN.replace(
                "      brief: build a", "      executor: planner\n      brief: build a"
            ),
        )
    )
    assert plan.modules[0].executor == "main"


def test_unknown_executor_is_rejected_with_the_choices(tmp_path: Path) -> None:
    body = VALID_PLAN.replace(
        "      brief: build a", "      executor: sideways\n      brief: build a"
    )
    with pytest.raises(PlanError, match="main .*sub"):
        load_plan(write_plan(tmp_path, body))


def test_the_two_tier_model_is_stated_where_agents_read_it() -> None:
    """The role contracts are what an agent actually reads, so they decide behaviour.

    The Planner contract used to say "Never implement modules yourself", which
    is the opposite of the Main Worker role. A doc that contradicts the model is
    worse than no doc, because agents follow it.
    """
    # The shipped copies are the only ones there are: this repository is the
    # package, not a harness project, so it keeps no contracts of its own.
    root = Path(__file__).resolve().parent.parent / "harness" / "templates"
    planner = (root / "agents" / "planner.md").read_text(encoding="utf-8")
    worker = (root / "agents" / "worker.md").read_text(encoding="utf-8")
    agents_md = (root / "AGENTS.md").read_text(encoding="utf-8")

    assert "Never implement modules yourself" not in planner
    assert "Main Worker" in planner
    assert "executor: main" in planner and "executor: sub" in planner
    assert "many plans" in planner
    assert "Sub-Worker" in worker
    assert "two tiers" in agents_md
    assert "Tier 3" not in agents_md


def test_nothing_tells_the_planner_it_may_not_implement() -> None:
    """The Planner is the Main Worker, and every surface it reads must agree.

    Reported after a real run: the Planner still said it does not do the work.
    The two-tier change had missed the briefing text and an integration shim,
    which are exactly the places a Planner actually reads.
    """
    root = Path(__file__).resolve().parent.parent
    surfaces = [
        "harness/templates/agents/planner.md",
        "harness/templates/AGENTS.md",
        "harness/plans.py",
        "harness/handoff.py",
        "integrations/README.md",
        "README.md",
    ]
    for rel in surfaces:
        text = (root / rel).read_text(encoding="utf-8")
        for forbidden in (
            "never write module code",
            "Never implement modules yourself",
            "Do not write module code",
        ):
            assert forbidden not in text, f"{rel} still forbids the Main Worker from working"

    # And the way out of a blocked task has to be spelled out where it happens.
    contract = (root / "harness" / "templates" / "agents" / "planner.md").read_text(
        encoding="utf-8"
    )
    assert "Take the module over" in contract
    assert "executor: main" in contract
    worker_src = (root / "harness" / "worker.py").read_text(encoding="utf-8")
    assert "set `executor: main`" in worker_src


# ---------------------------------------------------------------------------
# who does the work by default


def test_a_module_with_no_executor_stays_with_the_main_worker(tmp_path: Path) -> None:
    """The Planner works through the plan; delegation is the exception it opts into.

    This defaulted to `sub`, so a plan that said nothing handed every module to a
    Sub-Worker — which reads as "the Planner does not code", the opposite of the
    Main Worker role.
    """
    from harness.task import load_task, materialize

    plan = load_plan(write_plan(tmp_path, VALID_PLAN))
    assert [m.executor for m in plan.modules] == ["main", "main"]

    # And the task files materialized from it agree.
    materialize(plan, tmp_path / "tasks")
    assert load_task(tmp_path / "tasks", "a").executor == "main"


def test_plan_run_stops_at_the_module_that_is_yours(tmp_path: Path, capsys) -> None:
    """`plan run` is the Main Worker's loop, not a queue of Sub-Worker jobs.

    It used to filter the Main Worker's modules out and drain the delegated ones,
    so the flow it described had the Sub-Workers at the centre. Now it walks the
    plan in dependency order and hands back when the next module is the
    Planner's — nothing is spawned, and nothing later is started ahead of it.
    """
    from harness.cli import main
    from harness.plan import record_approval
    from harness.task import load_task, materialize

    body = VALID_PLAN.replace("      brief: build b", "      executor: sub\n      brief: build b")
    path = write_plan(tmp_path, body)
    plan = load_plan(path)
    materialize(plan, tmp_path / "tasks")
    record_approval(path, by="user")

    rc = main(
        ["plan", "run", str(path), "--tasks-dir", str(tmp_path / "tasks"), "--root", str(tmp_path)]
    )
    out = capsys.readouterr().out
    assert rc == 1, "the plan is not finished, and the exit code has to say so"
    assert "module 'a' (1/2) is yours to build" in out
    assert "nothing was spawned for it" in out
    assert "task done --id a --by planner" in out
    # The delegated module depends on 'a', and even an independent one would not
    # be started ahead of it: the loop stopped.
    assert load_task(tmp_path / "tasks", "b").status == "todo"
