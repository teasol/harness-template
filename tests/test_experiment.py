"""Tests for the experiment layer (Tier 1 <-> Tier 2).

Worktree tests run against a throwaway clone in ``tmp_path`` so they never
create branches or worktrees in the checkout under test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness import experiment as exp_mod
from harness.experiment import ExperimentError
from harness.plan import PlanError, load_plan

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git required")


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture()
def clone(tmp_path: Path) -> Path:
    """A disposable clone of the repo under test."""
    target = tmp_path / "repo"
    _run(Path("."), "git", "clone", "--quiet", ".", str(target))
    _run(target, "git", "config", "user.email", "test@example.invalid")
    _run(target, "git", "config", "user.name", "test")
    return target


# ---------------------------------------------------------------------------
# report schema: self-containment is the rule that keeps experiments comparable


def _plan_with_report_source(tmp_path: Path, source: str) -> Path:
    base = Path("plans/demo-pipeline.yaml").read_text(encoding="utf-8")
    base = base.replace("source: ${HARNESS_RESULTS_DIR}/stats.json", f"source: {source}")
    path = tmp_path / "plan.yaml"
    path.write_text(base, encoding="utf-8")
    return path


def test_demo_plan_declares_a_report() -> None:
    plan = load_plan("plans/demo-pipeline.yaml")
    assert plan.report.question
    names = [m.name for m in plan.report.metrics]
    assert "regression_slope" in names
    assert plan.report.artifacts == ["stats.json"]


def test_report_source_may_not_escape_the_experiment(tmp_path: Path) -> None:
    """Reading a sibling experiment's results would make the report unjudgeable alone."""
    path = _plan_with_report_source(tmp_path, "../exp-baseline/results/stats.json")
    with pytest.raises(PlanError, match="escapes via"):
        load_plan(path)


def test_report_source_may_not_be_absolute(tmp_path: Path) -> None:
    path = _plan_with_report_source(tmp_path, "/var/data/other.json")
    with pytest.raises(PlanError, match="absolute path"):
        load_plan(path)


def test_report_artifact_is_checked_too(tmp_path: Path) -> None:
    base = Path("plans/demo-pipeline.yaml").read_text(encoding="utf-8")
    base = base.replace("      - stats.json", "      - ../other/stats.json")
    path = tmp_path / "plan.yaml"
    path.write_text(base, encoding="utf-8")
    with pytest.raises(PlanError, match="escapes via"):
        load_plan(path)


MINIMAL_PLAN = """
plan:
  name: minimal
  goal: prove the schema
  report:
    metrics:
      - name: acc
{metric_line}        source: ${{HARNESS_RESULTS_DIR}}/m.json
  modules:
    - id: only
      brief: do the thing
      acceptance:
        steps:
          - id: s
            run: "true"
"""


def test_report_metric_requires_source_and_metric(tmp_path: Path) -> None:
    path = tmp_path / "plan.yaml"
    path.write_text(MINIMAL_PLAN.format(metric_line=""), encoding="utf-8")
    with pytest.raises(PlanError, match="requires 'metric'"):
        load_plan(path)
    # With the metric declared, the same plan parses.
    path.write_text(MINIMAL_PLAN.format(metric_line="        metric: val\n"), encoding="utf-8")
    assert load_plan(path).report.metrics[0].metric == "val"


def test_modules_may_not_share_a_deliverable(tmp_path: Path) -> None:
    """Two Workers owning one file is a planning error, caught before any work starts."""
    base = Path("plans/demo-pipeline.yaml").read_text(encoding="utf-8")
    base = base.replace(
        "        - src/demo_pipeline/stats.py", "        - src/demo_pipeline/data_gen.py", 1
    )
    path = tmp_path / "plan.yaml"
    path.write_text(base, encoding="utf-8")
    with pytest.raises(PlanError, match="owned by exactly one module"):
        load_plan(path)


# ---------------------------------------------------------------------------
# lifecycle


def test_invalid_experiment_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(ExperimentError, match="invalid experiment name"):
        exp_mod.start("Not A Slug", root=tmp_path)


@needs_git
def test_start_list_remove(clone: Path) -> None:
    assert exp_mod.list_experiments(clone) == []

    experiment = exp_mod.start("alpha", root=clone)
    assert experiment.branch == "exp/alpha"
    assert experiment.path.is_dir()
    assert experiment.plan_path.is_file()  # scaffolded for the Planner

    listed = exp_mod.list_experiments(clone)
    assert [e.name for e in listed] == ["alpha"]

    with pytest.raises(ExperimentError, match="already exists"):
        exp_mod.start("alpha", root=clone)

    exp_mod.remove("alpha", root=clone, force=True)
    assert exp_mod.list_experiments(clone) == []
    # The branch survives the worktree: it is the record of the attempt.
    branches = subprocess.run(
        ["git", "branch", "--list", "exp/alpha"],
        cwd=str(clone),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "exp/alpha" in branches


@needs_git
def test_experiments_are_isolated_from_each_other(clone: Path) -> None:
    """Two experiments get separate worktrees, so neither sees the other's files."""
    a = exp_mod.start("alpha", root=clone)
    b = exp_mod.start("beta", root=clone)
    assert a.path != b.path
    (a.path / "marker.txt").write_text("only-in-alpha", encoding="utf-8")
    assert not (b.path / "marker.txt").exists()


@needs_git
def test_find_unknown_experiment(clone: Path) -> None:
    with pytest.raises(ExperimentError, match="no experiment"):
        exp_mod.find_experiment("ghost", root=clone)


# ---------------------------------------------------------------------------
# reporting


@needs_git
def test_report_on_a_finished_experiment(clone: Path) -> None:
    """The spine is measured, and requested metrics come from real artifacts."""
    experiment = exp_mod.start("finished", root=clone)
    wt = experiment.path

    # Planner: adopt the shipped demo pipeline as this experiment's plan.
    shutil.copy("plans/demo-pipeline.yaml", wt / "plans/finished.yaml")
    plan_text = (wt / "plans/finished.yaml").read_text(encoding="utf-8")
    plan_text = plan_text.replace("name: demo-pipeline", "name: finished", 1)
    plan_text = plan_text.replace(
        "spec: configs/demo-pipeline.yaml", "spec: configs/finished.yaml", 1
    )
    (wt / "plans/finished.yaml").write_text(plan_text, encoding="utf-8")
    spec_text = Path("configs/demo-pipeline.yaml").read_text(encoding="utf-8")
    (wt / "configs/finished.yaml").write_text(
        spec_text.replace("name: demo-pipeline", "name: finished", 1), encoding="utf-8"
    )
    _run(wt, "git", "add", "-A")
    _run(wt, "git", "commit", "--quiet", "-m", "plan for finished")

    report = exp_mod.build_report("finished", root=clone, run_integration=True)

    assert report.integration == "PASSED"
    assert report.tasks_done == report.tasks_total == 2
    assert all(e["acceptance"] == "passed" for e in report.task_results)
    assert report.commit and report.dirty is False
    assert report.merge_ready is True
    assert report.question  # carried through from the plan

    values = {m.name: m.value for m in report.metrics}
    assert values["sample_count"] == 100
    assert 1.9 < values["regression_slope"] < 2.1
    assert not [m for m in report.metrics if m.error]

    written = exp_mod.write_experiment_report(report, wt, save=True)
    assert any(p.name == "report.json" for p in written)
    saved = wt / "experiments" / "finished" / "report.md"
    assert saved.is_file()
    assert "READY TO MERGE" in saved.read_text(encoding="utf-8")


@needs_git
def test_report_flags_an_unfinished_experiment(clone: Path) -> None:
    """A dirty worktree with unfinished modules must never read as merge-ready."""
    experiment = exp_mod.start("wip", root=clone)
    wt = experiment.path
    shutil.copy("plans/demo-pipeline.yaml", wt / "plans/wip.yaml")
    text = (wt / "plans/wip.yaml").read_text(encoding="utf-8")
    text = text.replace("name: demo-pipeline", "name: wip", 1)
    (wt / "plans/wip.yaml").write_text(text, encoding="utf-8")
    for stale in (wt / "tasks").glob("*.task.yaml"):
        stale.unlink()

    report = exp_mod.build_report("wip", root=clone, run_integration=False)

    assert report.merge_ready is False
    assert report.dirty is True
    assert any("not done" in c or "no tasks" in c for c in report.caveats)
    assert any("uncommitted" in c for c in report.caveats)
    assert "NOT READY" in exp_mod.report_markdown(report)


@needs_git
def test_report_requires_a_plan(clone: Path) -> None:
    exp_mod.start("naked", root=clone, scaffold=False)
    with pytest.raises(ExperimentError, match="has no plan"):
        exp_mod.build_report("naked", root=clone)


# ---------------------------------------------------------------------------
# plan-scoped accounting: tasks/ may hold task files from other plans


@needs_git
def test_report_ignores_tasks_from_other_plans(clone: Path) -> None:
    """Counting foreign task files reported an experiment complete when it was not.

    The shipped demo's finished board is inherited by every new project, so an
    unscoped count said "2/2 done" — and that number decides a merge.
    """
    experiment = exp_mod.start("scoped", root=clone)
    wt = experiment.path
    # A plan with two modules, neither materialized...
    shutil.copy("plans/demo-pipeline.yaml", wt / "plans/scoped.yaml")
    text = (wt / "plans/scoped.yaml").read_text(encoding="utf-8")
    text = text.replace("name: demo-pipeline", "name: scoped", 1)
    text = text.replace("id: data-gen", "id: alpha", 1).replace("id: stats", "id: beta", 1)
    text = text.replace("depends_on: [data-gen]", "depends_on: [alpha]", 1)
    (wt / "plans/scoped.yaml").write_text(text, encoding="utf-8")
    # ...while tasks/ still holds the demo's *finished* board.
    assert (wt / "tasks" / "data-gen.task.yaml").is_file()

    report = exp_mod.build_report("scoped", root=clone, run_integration=False)

    assert report.tasks_total == 2
    assert report.tasks_done == 0  # not 2: the demo's tasks are not this plan's
    assert report.merge_ready is False
    assert any("never materialized" in c for c in report.caveats)
    assert any("not part of this plan" in c for c in report.caveats)


@needs_git
def test_not_ready_always_states_a_reason(clone: Path) -> None:
    """A verdict the researcher cannot explain is not a decision aid."""
    experiment = exp_mod.start("unexplained", root=clone)
    wt = experiment.path
    shutil.copy("plans/demo-pipeline.yaml", wt / "plans/unexplained.yaml")
    text = (wt / "plans/unexplained.yaml").read_text(encoding="utf-8")
    (wt / "plans/unexplained.yaml").write_text(
        text.replace("name: demo-pipeline", "name: unexplained", 1), encoding="utf-8"
    )

    report = exp_mod.build_report("unexplained", root=clone, run_integration=False)

    assert not report.merge_ready
    assert report.blockers, "NOT READY must always come with stated blockers"
    assert "### Why not ready" in exp_mod.report_markdown(report)


@needs_git
def test_start_scaffolds_an_integration_spec(clone: Path) -> None:
    """The scaffold must not point at a file it forgot to create."""
    experiment = exp_mod.start("scaffolded", root=clone)
    assert (experiment.path / "configs" / "scaffolded.yaml").is_file()


@needs_git
def test_a_scaffold_is_not_a_plan(clone: Path) -> None:
    """Validating an untouched scaffold must fail, or TODOs read as a plan."""
    experiment = exp_mod.start("stub", root=clone)
    with pytest.raises(PlanError, match="still the scaffold"):
        load_plan(experiment.plan_path)
    with pytest.raises(ExperimentError, match="still the scaffold"):
        exp_mod.build_report("stub", root=clone, run_integration=False)
