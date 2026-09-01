"""Tests for the plan layer (Tier 1 <-> Tier 2).

Worktree tests run against a throwaway clone in ``tmp_path`` so they never
create plans or worktrees in the checkout under test.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness import plan as plan_mod
from harness import plans as plans_mod
from harness import task as task_mod
from harness.plan import PlanError, load_plan
from harness.plans import WorkPlanError

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
# report schema: self-containment is the rule that keeps plans comparable


def _plan_with_report_source(tmp_path: Path, source: str) -> Path:
    base = DEMO_PLAN.read_text(encoding="utf-8")
    base = base.replace("source: ${HARNESS_RESULTS_DIR}/stats.json", f"source: {source}")
    path = tmp_path / "plan.yaml"
    path.write_text(base, encoding="utf-8")
    return path


def test_demo_plan_declares_a_report() -> None:
    plan = load_plan(DEMO_PLAN)
    assert plan.goal
    names = [m.name for m in plan.report.metrics]
    assert "regression_slope" in names
    assert plan.report.artifacts == ["stats.json"]


def test_report_source_may_not_escape_the_plan(tmp_path: Path) -> None:
    """Reading a sibling plan's results would make the report unjudgeable alone."""
    path = _plan_with_report_source(tmp_path, "../other-plan/results/stats.json")
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
  integration: {{spec: configs/minimal.yaml}}
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
    (tmp_path / "configs").mkdir(exist_ok=True)
    (tmp_path / "configs" / "minimal.yaml").write_text(
        "name: minimal\nsteps:\n  - id: ok\n    run: 'true'\n", encoding="utf-8"
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


def test_invalid_plan_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(WorkPlanError, match="invalid plan name"):
        plans_mod.start("Not A Slug", root=tmp_path)


@needs_git
def test_start_list_remove(clone: Path) -> None:
    assert plans_mod.list_plans(clone) == []

    work = plans_mod.start("alpha", root=clone)
    assert work.git_branch == "alpha"
    assert work.path.is_dir()
    assert work.plan_path.is_file()  # scaffolded for the Planner

    listed = plans_mod.list_plans(clone)
    assert [e.name for e in listed] == ["alpha"]

    with pytest.raises(WorkPlanError, match="already exists"):
        plans_mod.start("alpha", root=clone)

    plans_mod.remove("alpha", root=clone, force=True)
    assert plans_mod.list_plans(clone) == []
    # The git branch survives the worktree: it is the record of the attempt.
    git_branches = subprocess.run(
        ["git", "branch", "--list", "alpha"],
        cwd=str(clone),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "alpha" in git_branches


@needs_git
def test_plans_are_isolated_from_each_other(clone: Path) -> None:
    """Two plans get separate worktrees, so neither sees the other's files."""
    a = plans_mod.start("alpha", root=clone)
    b = plans_mod.start("beta", root=clone)
    assert a.path != b.path
    (a.path / "marker.txt").write_text("only-in-alpha", encoding="utf-8")
    assert not (b.path / "marker.txt").exists()


@needs_git
def test_find_unknown_plan(clone: Path) -> None:
    with pytest.raises(WorkPlanError, match="no plan"):
        plans_mod.find_plan("ghost", root=clone)


# ---------------------------------------------------------------------------
# reporting


@needs_git
def test_report_on_a_finished_plan(clone: Path) -> None:
    """The spine is measured, and requested metrics come from real artifacts."""
    work = plans_mod.start("finished", root=clone)
    wt = work.path

    # Planner: adopt the demo pipeline fixture as the plan here.
    shutil.copy(DEMO_PLAN, wt / "plans/finished.yaml")
    plan_text = (wt / "plans/finished.yaml").read_text(encoding="utf-8")
    plan_text = plan_text.replace("name: demo-pipeline", "name: finished", 1)
    plan_text = plan_text.replace("spec: configs/demo.yaml", "spec: configs/finished.yaml", 1)
    (wt / "plans/finished.yaml").write_text(plan_text, encoding="utf-8")
    spec_text = DEMO_SPEC.read_text(encoding="utf-8")
    (wt / "configs/finished.yaml").write_text(
        spec_text.replace("name: demo-pipeline", "name: finished", 1), encoding="utf-8"
    )

    # Materialize and complete all tasks for this plan
    import yaml

    plan = load_plan(wt / "plans/finished.yaml")
    task_mod.materialize(plan, wt / "tasks")
    for path in (wt / "tasks").glob("*.task.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["task"]["status"] = "done"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    _run(wt, "git", "add", "-A")
    _run(wt, "git", "commit", "--quiet", "-m", "plan and tasks for finished")

    report = plans_mod.build_report("finished", root=clone, run_integration=True)

    assert report.integration == "PASSED"
    assert report.tasks_done == report.tasks_total == 2
    assert all(e["acceptance"] == "passed" for e in report.task_results)
    assert report.commit and report.dirty is False
    assert report.merge_ready is True
    assert report.goal  # carried through from the plan

    values = {m.name: m.value for m in report.metrics}
    assert values["sample_count"] == 42
    assert not [m for m in report.metrics if m.error]

    written = plans_mod.write_plan_report(report, wt, save=True)
    assert any(p.name == "report.json" for p in written)
    saved = wt / "plans" / "finished" / "report.md"
    assert saved.is_file()
    assert "READY TO MERGE" in saved.read_text(encoding="utf-8")


@needs_git
def test_report_flags_an_unfinished_plan(clone: Path) -> None:
    """A dirty worktree with unfinished modules must never read as merge-ready."""
    work = plans_mod.start("wip", root=clone)
    wt = work.path
    shutil.copy(DEMO_PLAN, wt / "plans/wip.yaml")
    text = (wt / "plans/wip.yaml").read_text(encoding="utf-8")
    text = text.replace("name: demo-pipeline", "name: wip", 1)
    (wt / "plans/wip.yaml").write_text(text, encoding="utf-8")
    for stale in (wt / "tasks").glob("*.task.yaml"):
        stale.unlink()

    report = plans_mod.build_report("wip", root=clone, run_integration=False)

    assert report.merge_ready is False
    assert report.dirty is True
    assert any("not done" in c or "no tasks" in c for c in report.caveats)
    assert any("uncommitted" in c for c in report.caveats)
    assert "NOT READY" in plans_mod.report_markdown(report)


@needs_git
def test_report_requires_a_plan(clone: Path) -> None:
    plans_mod.start("naked", root=clone, scaffold=False)
    with pytest.raises(WorkPlanError, match="has no plan"):
        plans_mod.build_report("naked", root=clone)


# ---------------------------------------------------------------------------
# plan-scoped accounting: tasks/ may hold task files from other plans


@needs_git
def test_report_ignores_tasks_from_other_plans(clone: Path) -> None:
    """Counting foreign task files reported a plan complete when it was not."""
    work = plans_mod.start("scoped", root=clone)
    wt = work.path
    # A plan with two modules, neither materialized...
    shutil.copy(DEMO_PLAN, wt / "plans/scoped.yaml")
    text = (wt / "plans/scoped.yaml").read_text(encoding="utf-8")
    text = text.replace("name: demo-pipeline", "name: scoped", 1)
    text = text.replace("id: data-gen", "id: alpha", 1).replace("id: stats", "id: beta", 1)
    text = text.replace("depends_on: [data-gen]", "depends_on: [alpha]", 1)
    (wt / "plans/scoped.yaml").write_text(text, encoding="utf-8")
    # ...while tasks/ holds a task from another plan.
    (wt / "tasks").mkdir(parents=True, exist_ok=True)
    (wt / "tasks" / "foreign.task.yaml").write_text(
        "task:\n  id: foreign\n  plan: other-plan\n  status: done\n  acceptance:\n    steps: []\n",
        encoding="utf-8",
    )

    report = plans_mod.build_report("scoped", root=clone, run_integration=False)

    assert report.tasks_total == 2
    assert report.tasks_done == 0  # not 2: foreign tasks are not this plan's
    assert report.merge_ready is False
    assert any("never materialized" in c for c in report.caveats)
    assert any("not part of this plan" in c for c in report.caveats)


@needs_git
def test_not_ready_always_states_a_reason(clone: Path) -> None:
    """A verdict the user cannot explain is not a decision aid."""
    work = plans_mod.start("unexplained", root=clone)
    wt = work.path
    shutil.copy(DEMO_PLAN, wt / "plans/unexplained.yaml")
    text = (wt / "plans/unexplained.yaml").read_text(encoding="utf-8")
    (wt / "plans/unexplained.yaml").write_text(
        text.replace("name: demo-pipeline", "name: unexplained", 1), encoding="utf-8"
    )

    report = plans_mod.build_report("unexplained", root=clone, run_integration=False)

    assert not report.merge_ready
    assert report.blockers, "NOT READY must always come with stated blockers"
    assert "### Why not ready" in plans_mod.report_markdown(report)


@needs_git
def test_start_scaffolds_an_integration_spec(clone: Path) -> None:
    """The scaffold must not point at a file it forgot to create."""
    work = plans_mod.start("scaffolded", root=clone)
    assert (work.path / "configs" / "scaffolded.yaml").is_file()


@needs_git
def test_a_scaffold_is_not_a_plan(clone: Path) -> None:
    """Validating an untouched scaffold must fail, or TODOs read as a plan."""
    work = plans_mod.start("stub", root=clone)
    with pytest.raises(PlanError, match="still the scaffold"):
        load_plan(work.plan_path)
    with pytest.raises(WorkPlanError, match="still the scaffold"):
        plans_mod.build_report("stub", root=clone, run_integration=False)


# ---------------------------------------------------------------------------
# orientation: `harness status` must name the right next step at every stage


def test_status_on_the_uninstantiated_template(tmp_path: Path) -> None:
    status = plans_mod.project_status(tmp_path, cwd=tmp_path)
    assert not status.instantiated
    assert "harness init" in " ".join(status.next_steps)


@needs_git
def test_status_walks_the_whole_flow(clone: Path) -> None:
    """One assertion per stage a newcomer can be standing in."""
    import yaml

    # Instantiated, nothing started yet.
    (clone / "pyproject.toml").write_text('name = "myproj"\n', encoding="utf-8")
    status = plans_mod.project_status(clone, cwd=clone)
    assert status.instantiated and not status.plans
    # No Planner registered yet, so that is the only step — the plan is the
    # Planner's to start.
    assert "harness create -n" in " ".join(status.next_steps)
    assert "harness plan new" not in " ".join(status.next_steps)

    # Opened: the plan scaffold is the first thing in the way.
    created = plans_mod.start("flow", root=clone)
    wt = created.path
    states = {e.name: e for e in plans_mod.project_status(clone, cwd=clone).plans}
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

    # A valid plan nobody has agreed to is a proposal, and the state says so —
    # this is the step where the Planner has to explain what it intends.
    states = {e.name: e for e in plans_mod.project_status(clone, cwd=clone).plans}
    assert states["flow"].state == "needs agreement"
    assert "plan approve" in states["flow"].next_command
    assert "the user runs this" in states["flow"].next_command

    plan_mod.record_approval(wt / "plans/flow.yaml", by="user")
    states = {e.name: e for e in plans_mod.project_status(clone, cwd=clone).plans}
    assert states["flow"].state == "not materialized"
    assert "materialize" in states["flow"].next_command

    # Materialized, nothing built.
    plan = load_plan(wt / "plans/flow.yaml")
    task_mod.materialize(plan, wt / "tasks")
    states = {e.name: e for e in plans_mod.project_status(clone, cwd=clone).plans}
    assert states["flow"].state == "building"
    assert "plan run" in states["flow"].next_command

    # A worker gave up.
    blocked = wt / "tasks" / "data-gen.task.yaml"
    data = yaml.safe_load(blocked.read_text(encoding="utf-8"))
    data["task"]["status"] = "blocked"
    blocked.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    states = {e.name: e for e in plans_mod.project_status(clone, cwd=clone).plans}
    assert states["flow"].state == "blocked"

    # Everything done: time to report, and only then to merge.
    for path in (wt / "tasks").glob("*.task.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["task"]["status"] = "done"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    status = plans_mod.project_status(clone, cwd=clone)
    assert status.plans[0].state == "ready to report"
    assert "harness report" in " ".join(status.next_steps)
    assert any("git merge" in step for step in status.next_steps)


@needs_git
def test_status_knows_which_worktree_you_are_in(clone: Path) -> None:
    work = plans_mod.start("here", root=clone)
    assert plans_mod.project_status(clone, cwd=clone).here is None
    assert plans_mod.project_status(clone, cwd=work.path).here == "here"


@needs_git
def test_status_reports_the_worker_adapter(clone: Path) -> None:
    """A manual adapter means no Workers are spawned; status must say so."""
    (clone / "pyproject.toml").write_text('name = "p"\n', encoding="utf-8")
    plans_mod.start("w", root=clone)
    assert plans_mod.project_status(clone, cwd=clone).worker_adapter == "manual"

    (clone / "configs" / "agents.yaml").write_text(
        "planner:\n  adapter: cli\n  command: 'true'\nworker:\n  adapter: cli\n  command: 'true'\n",
        encoding="utf-8",
    )
    status = plans_mod.project_status(clone, cwd=clone)
    assert status.worker_adapter == "cli"
    assert status.planner_tier["adapter"] == "cli"


# ---------------------------------------------------------------------------
# spawning a Planner (Tier 1 -> Tier 2)


@needs_git
def test_the_briefing_keeps_one_shape(clone: Path) -> None:
    """Same sections in the same order, whatever state the plan is in.

    A document that changes shape is one you must re-read; this one you
    re-run and skim.
    """
    sections = ["# Planner briefing:", "## The work", "## State", "## Next", "## Your role"]

    plans_mod.start("shape", root=clone)
    fresh = plans_mod.planner_brief("shape", root=clone)

    created = plans_mod.find_plan("shape", clone)
    (created.path / "configs" / "shape.yaml").write_text(
        "name: shape\nsteps:\n  - id: ok\n    run: 'true'\n", encoding="utf-8"
    )
    created.plan_path.write_text(
        "plan:\n  name: shape\n  goal: make the loader stop guessing\n"
        "  integration: {spec: configs/shape.yaml}\n"
        "  modules:\n    - id: m\n      brief: b\n      acceptance:\n"
        "        steps:\n          - id: s\n            run: 'true'\n            checks: []\n",
        encoding="utf-8",
    )
    with_goal = plans_mod.planner_brief("shape", root=clone)
    assert "make the loader stop guessing" in with_goal

    for brief in (fresh, with_goal):
        positions = [brief.index(s) for s in sections]
        assert positions == sorted(positions), "sections must keep their order"
    # The Next command always names a real action, never the briefing itself.
    for brief in (fresh, with_goal):
        nxt = brief.split("## Next")[1]
        assert "planner brief" not in nxt.split("## Your role")[0]


@needs_git
def test_starting_a_plan_registers_its_planner(clone: Path) -> None:
    """One plan, one Planner — so starting one is registering one."""
    plans_mod.start("owned", root=clone)
    # cmd_plan_new does the registering; do the same here.
    plans_mod.register_planner("owned", "planner", root=clone, model="m", effort="high")
    registered = plans_mod.planner_of(plans_mod.find_plan("owned", clone))
    assert registered["planner"] == "planner" and registered["model"] == "m"


# ---------------------------------------------------------------------------
# --no-run must reuse evidence, not discard it


def _finished_plan(clone: Path, name: str = "reuse") -> object:
    """A plan whose single module is done and whose integration passes."""
    work = plans_mod.start(
        name,
        root=clone,
    )
    work_root = work.path
    (work_root / "configs").mkdir(exist_ok=True)
    (work_root / "src").mkdir(exist_ok=True)
    (work_root / "src" / "widget.py").write_text("x = 1\n", encoding="utf-8")
    (work_root / "configs" / f"{name}.yaml").write_text(
        f"""
name: {name}
steps:
  - id: emit
    run: >-
      mkdir -p "${{HARNESS_RESULTS_DIR}}" &&
      echo {{\\"score\\": 0.5}} > "${{HARNESS_RESULTS_DIR}}/metrics.json"
    checks:
      - type: json_metric
        path: ${{HARNESS_RESULTS_DIR}}/metrics.json
        metric: score
        min: 0.0
""",
        encoding="utf-8",
    )
    (work_root / "plans").mkdir(exist_ok=True)
    work.plan_path.write_text(
        f"""
plan:
  name: {name}
  goal: prove the reuse path works
  report:
    question: does it hold?
    metrics:
      - name: score
        source: ${{HARNESS_RESULTS_DIR}}/metrics.json
        metric: score
  integration:
    spec: configs/{name}.yaml
  modules:
    - id: widget
      title: widget
      deliverables: [src/widget.py]
      brief: make src/widget.py
      acceptance:
        steps:
          - id: check
            run: test -f src/widget.py
            checks:
              - type: file_exists
                path: src/widget.py
""",
        encoding="utf-8",
    )
    from harness.plan import load_plan as _load_plan
    from harness.task import complete, materialize

    materialize(_load_plan(work.plan_path), work_root / "tasks")
    complete(work_root / "tasks", "widget", worker="w", root=work_root)
    return work


@needs_git
def test_no_run_reuses_the_last_integration_run(clone: Path) -> None:
    """Producing a report must not mean paying for the whole integration again."""
    _finished_plan(clone)

    fresh = plans_mod.build_report("reuse", root=clone)
    assert fresh.integration == "PASSED"
    assert fresh.integration_ok
    assert [m.value for m in fresh.metrics] == [0.5]

    reused = plans_mod.build_report("reuse", root=clone, run_integration=False)
    assert reused.integration.startswith("reused PASSED")
    assert reused.integration_ok
    # The numbers are really there, not "no integration run to read from".
    assert [m.value for m in reused.metrics] == [0.5]
    assert any("was not re-run" in c for c in reused.caveats)


@needs_git
def test_no_run_with_nothing_to_reuse_says_so(clone: Path) -> None:
    plans_mod.start(
        "empty",
        root=clone,
    )
    work = plans_mod.find_plan("empty", clone)
    (work.path / "plans").mkdir(exist_ok=True)
    work.plan_path.write_text(
        """
plan:
  name: empty
  goal: nothing has run yet
  integration:
    spec: configs/empty.yaml
  modules:
    - id: m
      title: m
      brief: b
      acceptance:
        steps:
          - id: s
            run: "true"
            checks: []
""",
        encoding="utf-8",
    )
    (work.path / "configs").mkdir(exist_ok=True)
    (work.path / "configs" / "empty.yaml").write_text(
        "name: empty\nsteps:\n  - id: s\n    run: 'true'\n    checks: []\n", encoding="utf-8"
    )
    report = plans_mod.build_report("empty", root=clone, run_integration=False)
    assert "no previous run" in report.integration
    assert not report.integration_ok
    assert not report.merge_ready


@needs_git
def test_reused_evidence_from_another_commit_blocks_the_merge(clone: Path) -> None:
    """It passed — for other code. That is not evidence about this commit.

    Silently accepting it would let a report certify a commit the integration
    never ran against, which is the exact failure the reuse shortcut invites.
    """
    work = _finished_plan(clone, "stale")
    plans_mod.build_report("stale", root=clone)  # produces the run to reuse

    # The code moves on after that run.
    work_root = work.path
    (work_root / "src" / "widget.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "move on"], cwd=work_root, check=True, capture_output=True
    )

    report = plans_mod.build_report("stale", root=clone, run_integration=False)
    assert report.integration_ok, "the reused run itself did pass"
    assert report.integration_stale
    assert not report.merge_ready
    assert any("different commit" in b for b in report.blockers)
    assert any("does not describe this code" in c for c in report.caveats)


@needs_git
def test_a_plan_approved_by_its_own_planner_is_flagged(clone: Path) -> None:
    """Self-approval passes the check and defeats it, so the report says so."""
    work = _finished_plan(clone, "selfapproved")
    plan_mod.record_approval(work.plan_path, by="planner")
    plans_mod.register_planner("selfapproved", "planner", root=clone, model="m", require_model=True)

    report = plans_mod.build_report("selfapproved", root=clone, run_integration=False)
    assert any("is the Planner itself" in c for c in report.caveats)

    # A different approver is not flagged.
    plan_mod.record_approval(work.plan_path, by="user")
    report = plans_mod.build_report("selfapproved", root=clone, run_integration=False)
    assert not any("is the Planner itself" in c for c in report.caveats)


# ---------------------------------------------------------------------------
# addressing a plan: the name is the handle, the path still works


@needs_git
def test_a_plan_is_addressable_by_name(clone: Path) -> None:
    """`plan validate fix-loader`, not `plan validate plans/fix-loader.yaml`.

    A plan lives in its own worktree, so the path is something the reader has to
    reconstruct. The name is what they already have.
    """
    created = plans_mod.start("named", root=clone)
    assert plans_mod.resolve_plan_path("named", clone) == created.plan_path

    # A path stays a path — a Planner inside its own worktree has one, and the
    # demo plan belongs to no worktree.
    as_path = str(created.plan_path)
    assert plans_mod.resolve_plan_path(as_path, clone) == Path(as_path)

    # An unknown name resolves under the root's own plans dir rather than
    # raising: the error the caller gets should be about the missing plan file.
    unknown = plans_mod.resolve_plan_path("ghost", clone)
    assert unknown.name == "ghost.yaml" and not unknown.is_file()


@needs_git
def test_plan_state_names_commands_by_plan_name(clone: Path) -> None:
    """The command `status` prints must be runnable from where it is read."""
    plans_mod.start("addressed", root=clone)
    state = plans_mod.project_status(clone, cwd=clone).plans[0]
    assert "addressed" in state.next_command
    assert "plans/addressed.yaml" not in state.next_command
