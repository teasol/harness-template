"""Experiments — the Tier 1 ↔ Tier 2 boundary.

An experiment is one research hypothesis, developed on its own branch in its
own git worktree. The Planner works there start to finish; the researcher
reads the resulting report and decides whether to merge. The harness never
merges: choosing which hypothesis enters the record is the researcher's
judgement, and the one thing here that is not automated.

Worktrees, not just branches, because several experiments run at once and each
needs its own files on disk. Within one experiment Workers run sequentially,
so its task board stays coherent and dependency gates read current state.

A report is deliberately self-contained: it draws only on its own experiment's
artifacts. Comparing experiments is the researcher's job, done by collecting
finished reports — not something one experiment may reach across to do.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from harness.checks import CheckError, lookup_metric
from harness.plan import Plan, PlanError, load_plan
from harness.report import write_reports
from harness.reproduce import ReproduceError, reproduce
from harness.reproducibility import collect_provenance
from harness.runner import Runner
from harness.spec import SpecError, load_spec
from harness.task import load_board, verify_task

BRANCH_PREFIX = "exp/"
DEFAULT_WORKTREE_ROOT = ".experiments"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ExperimentError(RuntimeError):
    """Raised when an experiment cannot be created, found, or reported on."""


@dataclasses.dataclass
class Experiment:
    """One hypothesis under development: a branch, a worktree, a plan."""

    name: str
    branch: str
    path: Path
    head: str = ""

    @property
    def plan_path(self) -> Path:
        return self.path / "plans" / f"{self.name}.yaml"


@dataclasses.dataclass
class MetricValue:
    """A number the harness extracted, plus where it came from."""

    name: str
    value: Any = None
    source: str = ""
    metric: str = ""
    error: str | None = None


@dataclasses.dataclass
class ExperimentReport:
    """What the researcher reads to decide whether to merge."""

    experiment: str
    branch: str
    question: str
    merge_ready: bool
    commit: str | None = None
    dirty: bool | None = None
    integration: str = "not run"
    tasks_total: int = 0
    tasks_done: int = 0
    task_results: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    determinism: str = "not run"
    metrics: list[MetricValue] = dataclasses.field(default_factory=list)
    artifacts: list[str] = dataclasses.field(default_factory=list)
    caveats: list[str] = dataclasses.field(default_factory=list)
    blockers: list[str] = dataclasses.field(default_factory=list)
    provenance: dict[str, Any] = dataclasses.field(default_factory=dict)
    run_dir: str | None = None


# ---------------------------------------------------------------------------
# git plumbing


def _git(root: str | Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if check and proc.returncode != 0:
        raise ExperimentError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def list_experiments(root: str | Path = ".") -> list[Experiment]:
    """Every experiment worktree git knows about, sorted by name."""
    porcelain = _git(root, "worktree", "list", "--porcelain")
    experiments: list[Experiment] = []
    path: str | None = None
    head = ""
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            path, head = line[len("worktree ") :], ""
        elif line.startswith("HEAD "):
            head = line[len("HEAD ") :]
        elif line.startswith("branch ") and path is not None:
            ref = line[len("branch ") :]
            branch = ref.removeprefix("refs/heads/")
            if branch.startswith(BRANCH_PREFIX):
                experiments.append(
                    Experiment(
                        name=branch[len(BRANCH_PREFIX) :],
                        branch=branch,
                        path=Path(path),
                        head=head,
                    )
                )
            path = None
    return sorted(experiments, key=lambda e: e.name)


def find_experiment(name: str, root: str | Path = ".") -> Experiment:
    for experiment in list_experiments(root):
        if experiment.name == name:
            return experiment
    known = [e.name for e in list_experiments(root)]
    raise ExperimentError(f"no experiment '{name}'. known: {known or '(none)'}")


# ---------------------------------------------------------------------------
# lifecycle


PLAN_TEMPLATE = """\
# Plan for experiment '{name}' — written by the Planner, read by Workers.
#
# TODO(Planner): replace every TODO below, then run:
#   python -m harness plan validate plans/{name}.yaml
#   python -m harness plan materialize plans/{name}.yaml
plan:
  name: {name}
  goal: >
    TODO: one paragraph stating what this experiment is meant to establish.

  # What the researcher asked to see. Declare WHERE each number lives; the
  # harness extracts the value from the real artifact. Paths must stay inside
  # this experiment — a report may not read another experiment's results.
  report:
    question: |
      TODO: the researcher's instruction, verbatim.
    metrics:
      - name: TODO_metric
        source: ${{HARNESS_RESULTS_DIR}}/metrics.json
        metric: TODO.dotted.key
    artifacts: []

  integration:
    spec: configs/{name}.yaml

  modules:
    - id: TODO-module
      title: TODO
      depends_on: []
      deliverables: [src/TODO.py]
      contract:
        inputs: []
        outputs: []
      brief: |
        TODO: complete instructions for one Worker, assuming it reads nothing
        but this task file and the repository.
      constraints: []
      acceptance:
        steps:
          - id: TODO-check
            run: ${{HARNESS_PYTHON}} -c "print('TODO')"
            checks: []
"""


def start(
    name: str,
    root: str | Path = ".",
    worktree_root: str | Path = DEFAULT_WORKTREE_ROOT,
    base: str = "HEAD",
    scaffold: bool = True,
) -> Experiment:
    """Create an experiment: a branch and a worktree to develop it in."""
    if not NAME_RE.match(name):
        raise ExperimentError(
            f"invalid experiment name '{name}' — use lowercase letters, digits, and hyphens"
        )
    root = Path(root).resolve()
    branch = f"{BRANCH_PREFIX}{name}"
    worktree_root = Path(worktree_root)
    path = worktree_root if worktree_root.is_absolute() else root / worktree_root
    path = path / name
    if path.exists():
        raise ExperimentError(f"path already exists: {path}")
    existing = _git(root, "branch", "--list", branch)
    if existing:
        raise ExperimentError(f"branch '{branch}' already exists — pick another name")

    path.parent.mkdir(parents=True, exist_ok=True)
    _git(root, "worktree", "add", "-b", branch, str(path), base)

    experiment = Experiment(
        name=name, branch=branch, path=path, head=_git(path, "rev-parse", "HEAD")
    )
    if scaffold:
        if not experiment.plan_path.exists():
            experiment.plan_path.parent.mkdir(parents=True, exist_ok=True)
            experiment.plan_path.write_text(PLAN_TEMPLATE.format(name=name), encoding="utf-8")
        # Scaffold the integration spec the plan points at, so the Planner's
        # first validation error is about the TODOs it must fill in, not about
        # a file the scaffold neglected to create.
        spec_path = path / "configs" / f"{name}.yaml"
        if not spec_path.exists():
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(INTEGRATION_TEMPLATE.format(name=name), encoding="utf-8")
    return experiment


def remove(name: str, root: str | Path = ".", force: bool = False) -> Experiment:
    """Remove an experiment's worktree. The branch is kept — it is the record."""
    experiment = find_experiment(name, root)
    args = ["worktree", "remove", str(experiment.path)]
    if force:
        args.append("--force")
    _git(root, *args)
    return experiment


# ---------------------------------------------------------------------------
# reporting


def _resolve_plan(experiment: Experiment) -> Plan:
    if not experiment.plan_path.is_file():
        raise ExperimentError(
            f"experiment '{experiment.name}' has no plan at {experiment.plan_path}. "
            "The Planner writes it before anything can be reported."
        )
    try:
        return load_plan(experiment.plan_path)
    except PlanError as exc:
        raise ExperimentError(f"plan for '{experiment.name}' is invalid: {exc}") from exc


def _extract_metrics(plan: Plan, run_dir: Path | None) -> list[MetricValue]:
    """Read each declared metric out of the artifacts the run actually wrote."""
    values: list[MetricValue] = []
    for ref in plan.report.metrics:
        value = MetricValue(name=ref.name, source=ref.source, metric=ref.metric)
        if run_dir is None:
            value.error = "no integration run to read from"
            values.append(value)
            continue
        relative = ref.source.replace("${HARNESS_RESULTS_DIR}", str(run_dir))
        relative = relative.replace("$HARNESS_RESULTS_DIR", str(run_dir))
        path = Path(relative)
        if not path.is_absolute():
            path = run_dir / path
        if not path.is_file():
            value.error = f"source not found: {path}"
        else:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                value.value = lookup_metric(data, ref.metric)
            except (json.JSONDecodeError, CheckError) as exc:
                value.error = str(exc)
        values.append(value)
    return values


def build_report(
    name: str,
    root: str | Path = ".",
    run_integration: bool = True,
    check_determinism: bool = False,
) -> ExperimentReport:
    """Assemble the researcher's decision aid for one experiment.

    The spine — integration result, task acceptance, determinism, the commit
    to merge — is measured here, not narrated by an agent. The requested
    metrics are extracted from the artifacts the run produced.
    """
    experiment = find_experiment(name, root)
    plan = _resolve_plan(experiment)
    exp_root = experiment.path

    report = ExperimentReport(
        experiment=experiment.name,
        branch=experiment.branch,
        question=plan.report.question.strip(),
        merge_ready=False,
        artifacts=list(plan.report.artifacts),
        provenance=collect_provenance(exp_root, seed=None),
    )
    report.commit = report.provenance.get("git_commit")
    report.dirty = report.provenance.get("git_dirty")

    # --- task board: is every module actually finished, and still passing? ---
    # Scoped to *this plan's* modules. A tasks/ directory may hold task files
    # from other plans (the shipped demo, an earlier plan); counting those
    # would report an experiment as complete when none of its own modules
    # were built — and that number decides a merge.
    board = {t.id: t for t in load_board(exp_root / "tasks")}
    module_ids = [m.id for m in plan.modules]
    report.tasks_total = len(module_ids)
    report.tasks_done = sum(1 for i in module_ids if i in board and board[i].is_done)

    missing: list[str] = []
    for module_id in module_ids:
        task = board.get(module_id)
        if task is None:
            missing.append(module_id)
            report.task_results.append(
                {
                    "id": module_id,
                    "status": "unmaterialized",
                    "acceptance": "not run",
                    "worker": None,
                }
            )
            continue
        entry = {"id": task.id, "status": task.status, "acceptance": "not run"}
        if task.is_done:
            result = verify_task(task, root=exp_root, results_dir=exp_root / "results")
            entry["acceptance"] = "passed" if result.success else "FAILED"
        entry["worker"] = task.worker
        report.task_results.append(entry)

    if missing:
        report.caveats.append(f"module(s) never materialized into tasks: {missing}")
    if report.tasks_done < report.tasks_total:
        report.caveats.append(
            f"{report.tasks_total - report.tasks_done} of {report.tasks_total} module(s) not done"
        )
    failed = [e["id"] for e in report.task_results if e["acceptance"] == "FAILED"]
    if failed:
        report.caveats.append(f"task acceptance now failing: {failed}")
    orphans = sorted(set(board) - set(module_ids))
    if orphans:
        report.caveats.append(
            f"task file(s) not part of this plan, ignored for this report: {orphans}"
        )

    # --- integration: does the assembled whole work? ---
    run_dir: Path | None = None
    if plan.integration is None:
        report.integration = "no integration spec declared"
        report.caveats.append("plan declares no integration spec — the whole was never verified")
    elif run_integration:
        spec_path = _integration_path(exp_root, plan.integration)
        try:
            spec = load_spec(spec_path)
        except SpecError as exc:
            report.integration = f"spec error: {exc}"
        else:
            runner = Runner(root=exp_root, results_dir=exp_root / "results")
            result = runner.run(spec)
            write_reports(result)
            run_dir = Path(result.run_dir)
            report.run_dir = result.run_dir
            report.integration = "PASSED" if result.success else "FAILED"
    else:
        report.integration = "skipped (--no-run)"
        report.caveats.append("integration spec was not run for this report")

    # --- determinism (opt-in: a full re-run can be expensive) ---
    if check_determinism and plan.integration is not None:
        try:
            outcome = reproduce(
                load_spec(_integration_path(exp_root, plan.integration)),
                times=2,
                root=exp_root,
                results_dir=exp_root / "results" / "reproduce",
            )
            report.determinism = "REPRODUCIBLE" if outcome.reproducible else "NOT REPRODUCIBLE"
            if not outcome.reproducible:
                report.caveats.extend(f"nondeterministic: {d}" for d in outcome.differences)
        except (ReproduceError, SpecError) as exc:
            report.determinism = f"could not check: {exc}"
            report.caveats.append(f"determinism unverified: {exc}")
    elif plan.integration is not None:
        report.caveats.append("determinism not checked (pass --determinism to run the gate)")

    report.metrics = _extract_metrics(plan, run_dir)
    for value in report.metrics:
        if value.error:
            report.caveats.append(f"metric '{value.name}' unavailable: {value.error}")

    if report.dirty:
        report.caveats.append(
            "worktree has uncommitted changes — the reported commit does not contain them"
        )

    # Every blocker becomes a stated reason: a verdict the researcher cannot
    # explain is not a decision aid.
    blockers: list[str] = []
    if report.integration != "PASSED":
        blockers.append(f"integration did not pass ({report.integration})")
    if report.tasks_total == 0:
        blockers.append("the plan declares no modules")
    if report.tasks_done != report.tasks_total:
        blockers.append(f"{report.tasks_done}/{report.tasks_total} module(s) done")
    if failed:
        blockers.append(f"acceptance failing: {failed}")
    if report.dirty:
        blockers.append("uncommitted changes in the worktree")
    if report.determinism == "NOT REPRODUCIBLE":
        blockers.append("the experiment is not reproducible")

    report.merge_ready = not blockers
    report.blockers = blockers
    return report


def _integration_path(exp_root: Path, integration: str) -> Path:
    candidate = Path(integration)
    return candidate if candidate.is_absolute() else exp_root / candidate


# ---------------------------------------------------------------------------
# rendering


def report_markdown(report: ExperimentReport) -> str:
    lines = [
        f"# Experiment report: {report.experiment}",
        "",
    ]
    if report.question:
        lines += ["## Question", "", report.question, ""]

    verdict = "READY TO MERGE" if report.merge_ready else "NOT READY"
    commit = report.commit or "unknown"
    if report.dirty:
        commit += " (dirty worktree)"
    lines += [
        "## Verdict",
        "",
        f"**{verdict}** — merging is the researcher's call; the harness only reports.",
        "",
        f"- Branch: `{report.branch}`",
        f"- Merge commit: `{commit}`",
        f"- Integration: {report.integration}",
        f"- Tasks: {report.tasks_done}/{report.tasks_total} done",
        f"- Determinism: {report.determinism}",
        "",
    ]
    if report.blockers:
        lines += ["### Why not ready", ""] + [f"- {b}" for b in report.blockers] + [""]

    if report.task_results:
        lines += [
            "## Modules",
            "",
            "| Task | Status | Acceptance | Worker |",
            "| --- | --- | --- | --- |",
        ]
        for entry in report.task_results:
            lines.append(
                f"| `{entry['id']}` | {entry['status']} | {entry['acceptance']} "
                f"| {entry.get('worker') or '-'} |"
            )
        lines.append("")

    if report.metrics:
        lines += ["## Requested metrics", "", "| Metric | Value | Source |", "| --- | --- | --- |"]
        for value in report.metrics:
            shown = value.error or value.value
            lines.append(f"| {value.name} | {shown} | `{value.source}`: `{value.metric}` |")
        lines.append("")

    if report.artifacts:
        lines += ["## Artifacts", ""] + [f"- `{a}`" for a in report.artifacts] + [""]

    lines += ["## Not verified", ""]
    lines += [f"- {c}" for c in report.caveats] or ["- (nothing outstanding)"]
    lines.append("")

    prov = report.provenance
    lines += [
        "## Provenance",
        "",
        f"- Python: {prov.get('python_version')} (`{prov.get('python_executable')}`)",
        f"- Platform: {prov.get('platform')}",
        f"- Harness: {prov.get('harness_version')}",
        "",
    ]
    return "\n".join(lines)


def write_experiment_report(
    report: ExperimentReport, exp_root: str | Path, save: bool = False
) -> list[Path]:
    """Write the report under ``results/`` and, with ``save``, into the branch."""
    exp_root = Path(exp_root)
    written: list[Path] = []

    out_dir = exp_root / "results" / "experiments" / report.experiment
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    json_path.write_text(
        json.dumps(dataclasses.asdict(report), indent=2, default=str) + "\n", encoding="utf-8"
    )
    md_path = out_dir / "report.md"
    md_path.write_text(report_markdown(report), encoding="utf-8")
    written += [json_path, md_path]

    if save:
        saved_dir = exp_root / "experiments" / report.experiment
        saved_dir.mkdir(parents=True, exist_ok=True)
        saved = saved_dir / "report.md"
        saved.write_text(report_markdown(report), encoding="utf-8")
        written.append(saved)
    return written


# ---------------------------------------------------------------------------
# Planner registration


def planner_brief(name: str, root: str | Path = ".") -> str:
    """Everything a session needs to start acting as this experiment's Planner.

    Deliberately a plain command producing plain text: any agent runtime can be
    told to run it and follow the result. Tool-specific shims (a skill, a slash
    command) are thin optional wrappers around this, never a prerequisite.
    """
    experiment = find_experiment(name, root)
    exp_root = experiment.path
    contract = exp_root / "agents" / "planner.md"

    lines = [
        f"# You are the Planner for experiment '{experiment.name}'",
        "",
        "Read agents/planner.md and follow it. You own this experiment end to end:",
        "decompose the goal into modules, hand them to Workers, verify, and report",
        "back. You never write module code, and you never merge — merging is the",
        "researcher's decision.",
        "",
        "## Where you are",
        f"- Worktree: {exp_root}",
        f"- Branch:   {experiment.branch}",
        f"- Plan:     {experiment.plan_path}",
        f"- Contract: {contract}",
        "",
    ]

    plan_text = (
        experiment.plan_path.read_text(encoding="utf-8") if experiment.plan_path.is_file() else ""
    )
    if plan_text and "TODO(Planner)" in plan_text:
        lines += [
            "## State: the plan is still a scaffold",
            "",
            "Every TODO in the plan is yours to replace — the goal, the report",
            "metrics, and each module's brief, deliverables, and acceptance. Until",
            "then `plan validate` refuses it: a scaffold is not a plan.",
            "",
        ]
    elif not experiment.plan_path.is_file():
        lines += [
            "## State: no plan yet",
            "",
            "Write the plan first. It must declare, per module: depends_on, a typed",
            "contract, a complete brief, constraints, deliverables (one owner each),",
            "and machine-checkable acceptance. It must also declare `report:` — what",
            "the researcher asked to see, as *where each number lives*. Never write a",
            "value into the plan; the harness extracts it.",
            "",
        ]
    else:
        try:
            plan = load_plan(experiment.plan_path)
        except PlanError as exc:
            lines += ["## State: plan is invalid", "", f"    {exc}", ""]
        else:
            board = {t.id: t for t in load_board(exp_root / "tasks")}
            done = sum(1 for t in board.values() if t.is_done)
            lines += [
                "## State",
                "",
                f"- Goal: {plan.goal.strip()}",
                f"- Modules: {done}/{len(plan.modules)} done",
                f"- Integration spec: {plan.integration or '(none declared)'}",
                "",
                "| Module | Status | Worker | Depends on |",
                "| --- | --- | --- | --- |",
            ]
            for module_id in plan.topological_order():
                task = board.get(module_id)
                status = task.status if task else "unmaterialized"
                worker = (task.worker if task and task.worker else "-") or "-"
                deps = ", ".join(plan.module(module_id).depends_on) or "-"
                lines.append(f"| `{module_id}` | {status} | {worker} | {deps} |")
            lines.append("")

    lines += [
        "## Your commands",
        "",
        "```bash",
        f"cd {exp_root}",
        f"python -m harness plan validate plans/{experiment.name}.yaml",
        f"python -m harness plan materialize plans/{experiment.name}.yaml",
        "python -m harness task list                 # what is ready",
        "python -m harness task run --id <id>        # hand one module to a Worker",
        f"python -m harness plan run plans/{experiment.name}.yaml   # drain the ready queue",
        f"python -m harness exp report {experiment.name} --determinism --save",
        "```",
        "",
        "`task run` invokes the configured Worker adapter, verifies acceptance and",
        "deliverables, and retries with the real failure output until the attempt",
        "cap. Configure the adapter in configs/worker.yaml; the default writes a",
        "briefing for a human to hand to a Worker session.",
        "",
        "## When you are done",
        "",
        f"Run `harness exp report {experiment.name}`. It exits non-zero until the",
        "experiment is genuinely merge-ready. Then stop and hand back to the",
        "researcher — do not merge.",
        "",
    ]
    return "\n".join(lines)


def register_planner(name: str, label: str, root: str | Path = ".") -> Path:
    """Record who is driving an experiment, so idle ones are visible."""
    experiment = find_experiment(name, root)
    marker = experiment.path / "results" / "experiments" / experiment.name / "planner.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps({"experiment": experiment.name, "planner": label}, indent=2) + "\n",
        encoding="utf-8",
    )
    return marker


def planner_of(experiment: Experiment) -> str | None:
    marker = experiment.path / "results" / "experiments" / experiment.name / "planner.json"
    if not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8")).get("planner")
    except json.JSONDecodeError:
        return None


INTEGRATION_TEMPLATE = """\
# Integration spec for experiment '{name}' — verifies the ASSEMBLED whole once
# every module is done. TODO(Planner): replace the placeholder step.
name: {name}
description: Integration check for {name}
seed: 42

steps:
  - id: TODO-integration
    run: ${{HARNESS_PYTHON}} -c "print('TODO: run the assembled pipeline')"
    timeout: 60
    checks: []
"""
