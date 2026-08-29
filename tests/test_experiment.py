"""Tests for the experiment layer (Tier 1 <-> Tier 2).

Worktree tests run against a throwaway clone in ``tmp_path`` so they never
create branches or worktrees in the checkout under test.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from harness import experiment as exp_mod
from harness import task as task_mod
from harness.experiment import ExperimentError
from harness.plan import PlanError, load_plan

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
DEMO_PLAN = FIXTURES_DIR / "demo-pipeline.yaml"
DEMO_SPEC = FIXTURES_DIR / "demo-pipeline-spec.yaml"

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
    base = DEMO_PLAN.read_text(encoding="utf-8")
    base = base.replace("source: ${HARNESS_RESULTS_DIR}/stats.json", f"source: {source}")
    path = tmp_path / "plan.yaml"
    path.write_text(base, encoding="utf-8")
    return path


def test_demo_plan_declares_a_report() -> None:
    plan = load_plan(DEMO_PLAN)
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
    base = DEMO_PLAN.read_text(encoding="utf-8")
    base = base.replace("      - stats.json", "      - ../other/stats.json")
    path = tmp_path / "plan.yaml"
    path.write_text(base, encoding="utf-8")
    with pytest.raises(PlanError, match="escapes via"):
        load_plan(path)


MINIMAL_PLAN = """
plan:
  name: minimal
  goal: g
  report:
    question: q
    metrics:
      - name: m
        source: out.json
{metric_line}  modules:
    - id: m
      brief: b
      acceptance:
        steps:
          - id: s
            run: "true"
"""


def test_plan_report_accepts_metric_path_syntax(tmp_path: Path) -> None:
    path = tmp_path / "plan.yaml"
    path.write_text(
        MINIMAL_PLAN.format(metric_line="        metric: results.loss\n"), encoding="utf-8"
    )
    assert load_plan(path).report.metrics[0].metric == "results.loss"

    path.write_text(MINIMAL_PLAN.format(metric_line="        metric: val\n"), encoding="utf-8")
    assert load_plan(path).report.metrics[0].metric == "val"


def test_modules_may_not_share_a_deliverable(tmp_path: Path) -> None:
    """Two Workers owning one file is a planning error, caught before any work starts."""
    base = DEMO_PLAN.read_text(encoding="utf-8")
    base = base.replace("        - configs/demo.yaml", "        - scripts/demo_step.py", 1)
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

    # Planner: adopt the demo pipeline fixture as this experiment's plan.
    shutil.copy(DEMO_PLAN, wt / "plans/finished.yaml")
    plan_text = (wt / "plans/finished.yaml").read_text(encoding="utf-8")
    plan_text = plan_text.replace("name: demo-pipeline", "name: finished", 1)
    plan_text = plan_text.replace("spec: configs/demo.yaml", "spec: configs/finished.yaml", 1)
    (wt / "plans/finished.yaml").write_text(plan_text, encoding="utf-8")
    spec_text = DEMO_SPEC.read_text(encoding="utf-8")
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
    assert values["sample_count"] == 42
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
    shutil.copy(DEMO_PLAN, wt / "plans/wip.yaml")
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
    """Counting foreign task files reported an experiment complete when it was not."""
    experiment = exp_mod.start("scoped", root=clone)
    wt = experiment.path
    # A plan with two modules, neither materialized...
    shutil.copy(DEMO_PLAN, wt / "plans/scoped.yaml")
    text = (wt / "plans/scoped.yaml").read_text(encoding="utf-8")
    text = text.replace("name: demo-pipeline", "name: scoped", 1)
    text = text.replace("id: data-gen", "id: alpha", 1).replace("id: stats", "id: beta", 1)
    text = text.replace("depends_on: [data-gen]", "depends_on: [alpha]", 1)
    (wt / "plans/scoped.yaml").write_text(text, encoding="utf-8")
    # ...while tasks/ holds a task from another plan.
    (wt / "tasks" / "foreign.task.yaml").write_text(
        "task:\n  id: foreign\n  plan: other-plan\n  status: done\n  acceptance:\n    steps: []\n",
        encoding="utf-8",
    )

    report = exp_mod.build_report("scoped", root=clone, run_integration=False)

    assert report.tasks_total == 2
    assert report.tasks_done == 0  # not 2: foreign tasks are not this plan's
    assert report.merge_ready is False
    assert any("never materialized" in c for c in report.caveats)
    assert any("not part of this plan" in c for c in report.caveats)


@needs_git
def test_not_ready_always_states_a_reason(clone: Path) -> None:
    """A verdict the researcher cannot explain is not a decision aid."""
    experiment = exp_mod.start("unexplained", root=clone)
    wt = experiment.path
    shutil.copy(DEMO_PLAN, wt / "plans/unexplained.yaml")
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


# ---------------------------------------------------------------------------
# orientation: `harness status` must name the right next step at every stage


def test_status_on_the_uninstantiated_template(tmp_path: Path) -> None:
    status = exp_mod.project_status(tmp_path, cwd=tmp_path)
    assert not status.instantiated
    assert "harness init" in " ".join(status.next_steps)


@needs_git
def test_status_walks_the_whole_flow(clone: Path) -> None:
    """One assertion per stage a newcomer can be standing in."""
    import yaml

    # Instantiated, nothing started yet.
    (clone / "pyproject.toml").write_text('name = "myproj"\n', encoding="utf-8")
    status = exp_mod.project_status(clone, cwd=clone)
    assert status.instantiated and not status.experiments
    assert "exp start" in " ".join(status.next_steps)

    # Opened, but the question is not agreed yet — the first state of all.
    experiment = exp_mod.start("flow", root=clone)
    wt = experiment.path
    states = {e.name: e for e in exp_mod.project_status(clone, cwd=clone).experiments}
    assert states["flow"].state == "question unsettled"
    assert "exp question flow --set" in states["flow"].next_command

    # Question settled: now the scaffold is what stands in the way.
    exp_mod.set_question("flow", "does it hold?", root=clone)
    states = {e.name: e for e in exp_mod.project_status(clone, cwd=clone).experiments}
    assert states["flow"].state == "scaffold"
    assert "plan validate" in states["flow"].next_command

    # A real plan, but no task files yet.
    shutil.copy(DEMO_PLAN, wt / "plans/flow.yaml")
    text = (wt / "plans/flow.yaml").read_text(encoding="utf-8")
    text = text.replace("name: demo-pipeline", "name: flow", 1)
    text = text.replace("spec: configs/demo.yaml", "spec: configs/flow.yaml", 1)
    (wt / "plans/flow.yaml").write_text(text, encoding="utf-8")
    shutil.copy(DEMO_SPEC, wt / "configs/flow.yaml")
    for stale in (wt / "tasks").glob("*.task.yaml"):
        stale.unlink()
    states = {e.name: e for e in exp_mod.project_status(clone, cwd=clone).experiments}
    assert states["flow"].state == "not materialized"
    assert "materialize" in states["flow"].next_command

    # Materialized, nothing built.
    plan = load_plan(wt / "plans/flow.yaml")
    task_mod.materialize(plan, wt / "tasks")
    states = {e.name: e for e in exp_mod.project_status(clone, cwd=clone).experiments}
    assert states["flow"].state == "building"
    assert "plan run" in states["flow"].next_command

    # A worker gave up.
    blocked = wt / "tasks" / "data-gen.task.yaml"
    data = yaml.safe_load(blocked.read_text(encoding="utf-8"))
    data["task"]["status"] = "blocked"
    blocked.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    states = {e.name: e for e in exp_mod.project_status(clone, cwd=clone).experiments}
    assert states["flow"].state == "blocked"

    # Everything done: time to report, and only then to merge.
    for path in (wt / "tasks").glob("*.task.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["task"]["status"] = "done"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    status = exp_mod.project_status(clone, cwd=clone)
    assert status.experiments[0].state == "ready to report"
    assert "exp report" in " ".join(status.next_steps)
    assert any("git merge" in step for step in status.next_steps)


@needs_git
def test_status_knows_which_worktree_you_are_in(clone: Path) -> None:
    experiment = exp_mod.start("here", root=clone)
    assert exp_mod.project_status(clone, cwd=clone).here is None
    assert exp_mod.project_status(clone, cwd=experiment.path).here == "here"


@needs_git
def test_status_reports_the_worker_adapter(clone: Path) -> None:
    """A manual adapter means no Workers are spawned; status must say so."""
    (clone / "pyproject.toml").write_text('name = "p"\n', encoding="utf-8")
    exp_mod.start("w", root=clone)
    assert exp_mod.project_status(clone, cwd=clone).worker_adapter == "manual"

    (clone / "configs" / "agents.yaml").write_text(
        "planner:\n  adapter: cli\n  command: 'true'\nworker:\n  adapter: cli\n  command: 'true'\n",
        encoding="utf-8",
    )
    status = exp_mod.project_status(clone, cwd=clone)
    assert status.worker_adapter == "cli"
    assert status.planner_tier["adapter"] == "cli"


# ---------------------------------------------------------------------------
# spawning a Planner (Tier 1 -> Tier 2)


@needs_git
def test_manual_planner_stops_for_a_human(clone: Path) -> None:
    from harness.worker import AgentConfig

    exp_mod.start("manual-p", root=clone)
    outcome = exp_mod.run_planner("manual-p", AgentConfig(label="planner"), root=clone)
    assert outcome.status == "needs_human"
    assert Path(outcome.brief_path).is_file()


@needs_git
def test_planner_is_driven_until_the_experiment_is_reportable(clone: Path) -> None:
    """A Planner's definition of done is the experiment, not a single call."""
    from harness.worker import AgentConfig

    experiment = exp_mod.start("driven", root=clone, question="does it work?")
    wt = experiment.path
    shutil.copy(DEMO_PLAN, wt / "plans/driven.yaml")
    text = (wt / "plans/driven.yaml").read_text(encoding="utf-8")
    text = text.replace("name: demo-pipeline", "name: driven", 1)
    text = text.replace("spec: configs/demo.yaml", "spec: configs/driven.yaml", 1)
    (wt / "plans/driven.yaml").write_text(text, encoding="utf-8")
    shutil.copy(DEMO_SPEC, wt / "configs/driven.yaml")
    for stale in (wt / "tasks").glob("*.task.yaml"):
        stale.unlink()

    # A stub Planner: materializes on the first call, finishes on the second.
    script = wt / "stub-planner.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "cat > /dev/null\n"
        f"cd {wt}\n"
        "if [ ! -f .planned ]; then\n"
        f"  {sys.executable} -m harness plan materialize plans/driven.yaml >/dev/null\n"
        "  touch .planned\n"
        "else\n"
        "  for f in tasks/*.task.yaml; do\n"
        "    sed -i 's/^  status: todo/  status: done/' \"$f\"\n"
        "  done\n"
        "fi\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    config = AgentConfig(
        adapter="cli",
        platform="stub",
        model="big",
        effort="high",
        command=f"bash {script}",
        attempts=3,
        label="planner",
    )
    outcome = exp_mod.run_planner("driven", config, root=clone)

    assert outcome.succeeded, [a.state for a in outcome.attempts]
    assert [a.state for a in outcome.attempts][-1] == "ready to report"
    assert len(outcome.attempts) >= 2  # it took more than one call
    # Registration happened automatically, with the tier recorded.
    registered = exp_mod.planner_of(exp_mod.find_experiment("driven", clone))
    assert registered["model"] == "big" and registered["effort"] == "high"


@needs_git
def test_planner_gives_up_and_says_so(clone: Path) -> None:
    from harness.worker import AgentConfig

    exp_mod.start("stuck", root=clone, question="does it work?")
    config = AgentConfig(
        adapter="cli", command="true", attempts=2, label="planner", platform="stub"
    )
    outcome = exp_mod.run_planner("stuck", config, root=clone)
    assert outcome.status == "incomplete"
    assert len(outcome.attempts) == 2
    assert "researcher should look at it" in outcome.message


@needs_git
def test_the_question_reaches_the_planner(clone: Path) -> None:
    """A spawned Planner must read what the researcher actually asked."""
    question = "Are there more AABB-splittable primes than prime birthdays?"
    experiment = exp_mod.start("asked", root=clone, question=question)

    assert experiment.question == question
    assert experiment.question_path.is_file()  # committed with the experiment
    brief = exp_mod.planner_brief("asked", root=clone)
    assert "## Question" in brief
    assert question in brief
    # It is also seeded into the scaffold, where it becomes report.question.
    assert question in experiment.plan_path.read_text(encoding="utf-8")


@needs_git
def test_an_experiment_without_a_question_still_works(clone: Path) -> None:
    experiment = exp_mod.start("unasked", root=clone)
    assert experiment.question == ""
    assert "**Not settled yet.**" in exp_mod.planner_brief("unasked", root=clone)


# ---------------------------------------------------------------------------
# the question is optional: it usually gets sharper by talking it through


@needs_git
def test_an_experiment_can_start_before_its_question_is_settled(clone: Path) -> None:
    """Opening an experiment, then working out the question, is the normal path."""
    experiment = exp_mod.start("later", root=clone)
    assert experiment.question == ""

    brief = exp_mod.planner_brief("later", root=clone)
    # The skeleton is the same whatever the state; only the contents differ.
    for section in ("## Question", "## State", "## Next", "## Your role"):
        assert section in brief
    assert "**Not settled yet.**" in brief
    assert "Plan nothing and spawn no Worker until you agree" in brief
    assert "harness exp question later --set" in brief


@needs_git
def test_the_question_can_be_recorded_afterwards(clone: Path) -> None:
    exp_mod.start("later", root=clone)
    settled = "Do sparse heads keep 90% of the attention mass?"

    experiment = exp_mod.set_question("later", settled, root=clone)

    assert experiment.question == settled
    assert experiment.question_path.is_file()  # committed with the experiment
    brief = exp_mod.planner_brief("later", root=clone)
    for section in ("## Question", "## State", "## Next", "## Your role"):
        assert section in brief  # same sections, different contents
    assert settled in brief
    assert "Not settled yet" not in brief


@needs_git
def test_an_empty_question_is_refused(clone: Path) -> None:
    exp_mod.start("later", root=clone)
    with pytest.raises(ExperimentError, match="cannot be empty"):
        exp_mod.set_question("later", "   ", root=clone)


@needs_git
def test_spawning_a_planner_without_a_question_asks_for_one(clone: Path) -> None:
    """An unattended Planner cannot ask what is wanted, so it must not guess."""
    from harness.worker import AgentConfig

    exp_mod.start("unasked-run", root=clone)
    config = AgentConfig(adapter="cli", command="true", attempts=2, label="planner")

    outcome = exp_mod.run_planner("unasked-run", config, root=clone)

    assert outcome.status == "needs_human"
    assert not outcome.attempts  # nothing was spawned
    assert "no recorded question" in outcome.message
    assert "drive it interactively" in outcome.message


@needs_git
def test_the_briefing_keeps_one_shape(clone: Path) -> None:
    """Same sections in the same order, whatever state the experiment is in.

    A document that changes shape is one you must re-read; this one you
    re-run and skim.
    """
    sections = ["# Planner briefing:", "## Question", "## State", "## Next", "## Your role"]

    exp_mod.start("shape", root=clone)
    unsettled = exp_mod.planner_brief("shape", root=clone)

    exp_mod.set_question("shape", "does it hold?", root=clone)
    settled = exp_mod.planner_brief("shape", root=clone)

    for brief in (unsettled, settled):
        positions = [brief.index(s) for s in sections]
        assert positions == sorted(positions), "sections must keep their order"
    # The Next command always names a real action, never the briefing itself.
    for brief in (unsettled, settled):
        nxt = brief.split("## Next")[1]
        assert "planner brief" not in nxt.split("## Your role")[0]


@needs_git
def test_exp_start_registers_the_planner(clone: Path) -> None:
    """One experiment, one Planner — so starting one is registering one."""
    exp_mod.start("owned", root=clone)
    # cmd_exp_start does the registering; do the same here.
    exp_mod.register_planner("owned", "planner", root=clone, model="m", effort="high")
    registered = exp_mod.planner_of(exp_mod.find_experiment("owned", clone))
    assert registered["planner"] == "planner" and registered["model"] == "m"
