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

import contextlib
import dataclasses
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from harness import adoption as adoption_mod
from harness import planners as planners_mod
from harness import project as project_mod
from harness.checks import CheckError, lookup_metric
from harness.paths import (
    get_agents_config_path,
    get_configs_dir,
    get_plans_dir,
    get_tasks_dir,
)
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
        return get_plans_dir(self.path) / f"{self.name}.yaml"

    @property
    def question_path(self) -> Path:
        """The researcher's question, verbatim, committed with the experiment."""
        return self.path / "experiments" / self.name / "question.md"

    @property
    def question(self) -> str:
        if self.question_path.is_file():
            return self.question_path.read_text(encoding="utf-8").strip()
        return ""


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
    #: Whether the integration evidence — fresh or reused — actually passed.
    #: Kept separate from the human string above so merge-readiness never
    #: depends on matching prose.
    integration_ok: bool = False
    #: True when the evidence is a reused run from a different commit, so it
    #: passed for code that is not the code being reported on.
    integration_stale: bool = False
    tasks_total: int = 0
    tasks_done: int = 0
    task_results: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    determinism: str = "not run"
    metrics: list[MetricValue] = dataclasses.field(default_factory=list)
    artifacts: list[str] = dataclasses.field(default_factory=list)
    caveats: list[str] = dataclasses.field(default_factory=list)
    blockers: list[str] = dataclasses.field(default_factory=list)
    tiers: dict[str, Any] = dataclasses.field(default_factory=dict)
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
      {question}
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


#: Configuration a new worktree must inherit to behave like the project it
#: came from. These live under `.harness/`, which is untracked, so a fresh
#: worktree does not get them from git.
_INHERITED_CONFIGS = ("agents.yaml", "agent-platforms.yaml", "project.yaml")


def _inherit_agent_configs(root: Path, worktree: Path) -> list[str]:
    """Copy the project's agent configuration into a new experiment worktree.

    Without this the harness finds no ``agents.yaml`` in the worktree and falls
    back to the manual adapter — silently. ``plan run`` then writes briefings
    and stops, which is indistinguishable from success until you notice no
    Worker ever ran. Inheriting the configuration makes the worktree behave
    like the project it was cut from.
    """
    source = get_configs_dir(root)
    target = get_configs_dir(worktree)
    copied: list[str] = []
    for name in _INHERITED_CONFIGS:
        src = source / name
        if not src.is_file():
            continue
        dst = target / name
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(name)
    return copied


def start(
    name: str,
    root: str | Path = ".",
    worktree_root: str | Path = DEFAULT_WORKTREE_ROOT,
    base: str = "HEAD",
    scaffold: bool = True,
    question: str = "",
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
    _inherit_agent_configs(root, path)
    if question.strip():
        # An experiment starts from a question. Storing it verbatim means a
        # Planner spawned later reads what the researcher actually asked,
        # rather than a paraphrase that passed through someone's summary.
        experiment.question_path.parent.mkdir(parents=True, exist_ok=True)
        experiment.question_path.write_text(question.strip() + "\n", encoding="utf-8")
    if scaffold:
        if not experiment.plan_path.exists():
            experiment.plan_path.parent.mkdir(parents=True, exist_ok=True)
            experiment.plan_path.write_text(
                PLAN_TEMPLATE.format(
                    name=name,
                    question=(
                        question.strip() or "TODO: the researcher's instruction, verbatim."
                    ).replace("\n", "\n      "),
                ),
                encoding="utf-8",
            )
        # Scaffold the integration spec the plan points at, so the Planner's
        # first validation error is about the TODOs it must fill in, not about
        # a file the scaffold neglected to create.
        spec_path = get_configs_dir(path) / f"{name}.yaml"
        if not spec_path.exists():
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(INTEGRATION_TEMPLATE.format(name=name), encoding="utf-8")
    return experiment


def set_question(name: str, question: str, root: str | Path = ".") -> Experiment:
    """Record the question after the fact.

    An experiment often starts before its question is sharp: the researcher
    opens a Planner and they work out what is actually being asked. That
    conversation is the point, so the question is recorded when it settles
    rather than demanded up front.
    """
    experiment = find_experiment(name, root)
    if not question.strip():
        raise ExperimentError("the question cannot be empty")
    experiment.question_path.parent.mkdir(parents=True, exist_ok=True)
    experiment.question_path.write_text(question.strip() + "\n", encoding="utf-8")
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
    report.tiers = {
        "planner": planner_of(experiment) or _agent_tier(exp_root, "planner"),
        "worker": _agent_tier(exp_root, "worker"),
    }

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
            report.integration_ok = result.success
    else:
        # --no-run used to throw away a run that had just passed, so every
        # metric came back "no integration run to read from" and producing a
        # report meant paying for the whole integration again — hours of GPU in
        # the case that motivated this. Attach the last one instead, and say
        # exactly which run it is so nobody mistakes it for a fresh result.
        previous = _last_run(exp_root, plan.integration)
        if previous is None:
            report.integration = "skipped (--no-run), and no previous run to attach"
            report.caveats.append(
                "integration spec was not run, and no earlier run was found to read from"
            )
        else:
            run_dir = previous.path
            report.run_dir = str(previous.path)
            verdict = "PASSED" if previous.success else "FAILED"
            report.integration = f"reused {verdict} run from {previous.finished_at}"
            report.integration_ok = previous.success
            report.caveats.append(
                f"integration was not re-run: these numbers come from {previous.path.name}, "
                f"which {verdict.lower()} at {previous.finished_at}"
            )
            if previous.commit and report.commit and previous.commit != report.commit:
                report.integration_stale = True
                report.caveats.append(
                    f"the reused run was made at commit {previous.commit[:12]}, not the "
                    f"current {report.commit[:12]} — it does not describe this code"
                )

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

    # A result whose Planner model is unknown cannot be compared with any other
    # result, so the gap is stated rather than left to be noticed.
    if not ((report.tiers.get("planner") or {}).get("model") or _planner_model(experiment, root)):
        report.caveats.append(
            "Planner model not recorded — this run cannot be compared with another. "
            f"Register it: harness planner brief {name} --register <label> --model <model>"
        )

    # Every blocker becomes a stated reason: a verdict the researcher cannot
    # explain is not a decision aid.
    blockers: list[str] = []
    if not report.integration_ok:
        blockers.append(f"integration did not pass ({report.integration})")
    elif report.integration_stale:
        # It passed — for other code. That is not evidence about this commit.
        blockers.append("the reused integration run was made at a different commit")
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


@dataclasses.dataclass
class PreviousRun:
    """A finished integration run that a report can attach to instead of re-running."""

    path: Path
    success: bool
    finished_at: str
    commit: str | None


def _last_run(exp_root: Path, integration: str | None) -> PreviousRun | None:
    """The most recent completed run of this experiment's integration spec.

    Matched on the spec's name, so an unrelated spec's run in the same results
    directory is never mistaken for this experiment's evidence.
    """
    if integration is None:
        return None
    try:
        spec_name = load_spec(_integration_path(exp_root, integration)).name
    except (SpecError, ExperimentError, OSError):
        return None
    runs_dir = exp_root / "results" / "runs"
    if not runs_dir.is_dir():
        return None
    candidates = sorted(
        (d for d in runs_dir.glob(f"{spec_name}-*") if (d / "report.json").is_file()),
        key=lambda d: d.name,
        reverse=True,
    )
    for directory in candidates:
        try:
            data = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        return PreviousRun(
            path=directory,
            success=bool(data.get("success")),
            finished_at=str(data.get("finished_at", "unknown")),
            commit=(data.get("provenance") or {}).get("git_commit"),
        )
    return None


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

    planner = (report.tiers or {}).get("planner") or {}
    worker = (report.tiers or {}).get("worker") or {}
    if planner or worker:

        def _tier(entry: dict[str, Any], fallback: str) -> str:
            parts = [entry.get("model") or "", entry.get("effort") or ""]
            shown = " · ".join(p for p in parts if p)
            return shown or fallback

        lines += [
            "## Tiers",
            "",
            f"- Planner: {planner.get('planner') or 'unregistered'}"
            f" ({_tier(planner, 'model not recorded')})",
            f"- Workers: {worker.get('platform') or worker.get('adapter') or 'unknown'}"
            f" ({_tier(worker, 'model not recorded')})",
            "",
        ]

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


def _anything_reported(root: str | Path = ".") -> bool:
    """True once any experiment here has reached a reportable state.

    The adoption framing is for a project where nothing has been proven yet. Once
    one experiment has, repeating it in every briefing is noise.
    """
    try:
        return any(
            e.state == "ready to report"
            for e in (experiment_state(x) for x in list_experiments(root))
        )
    except ExperimentError:
        return False


def _planner_model(experiment: Experiment, root: str | Path = ".") -> str:
    """The model driving this experiment, from the marker or the registry.

    A registered Planner carries its own model, so an experiment linked to one
    is never "model not recorded" — the registry answers on its behalf.
    """
    marker = planner_of(experiment) or {}
    if marker.get("model"):
        return str(marker["model"])
    label = marker.get("planner")
    if label:
        try:
            return planners_mod.load(str(label), root).model
        except planners_mod.PlannerError:
            return ""
    return ""


def planner_brief(name: str, root: str | Path = ".") -> str:
    """The Planner's working briefing: question, state, and the next command.

    Always the same sections in the same order, whatever the experiment's
    state — only their contents differ. A document that changes shape is a
    document you have to re-read; this one you can re-run and skim.

    Deliberately a plain command producing plain text: any agent runtime can be
    told to run it and follow the result. Tool-specific shims (a skill, a slash
    command) are thin optional wrappers around this, never a prerequisite.
    """
    experiment = find_experiment(name, root)
    exp_root = experiment.path
    state = experiment_state(experiment)

    lines = [
        f"# Planner briefing: {experiment.name}",
        "",
        "## Question",
        "",
    ]
    if experiment.question:
        lines += [
            experiment.question,
            "",
            "Everything you do serves answering exactly this. Do not widen it, and",
            "do not narrow it; if it is genuinely ambiguous, pick the reading a",
            "careful colleague would and state the assumption in the plan's goal.",
        ]
    else:
        lines += [
            "**Not settled yet.** That is normal — a question gets sharper by",
            "talking it through. Work it out with the researcher first: what is",
            "being asked, what would count as an answer, what they want reported.",
            "Plan nothing and spawn no Worker until you agree, then record it",
            "verbatim so it survives this session and reaches the report.",
        ]
    lines += [""]
    # A project that predates the harness needs saying so, once, to the Planner
    # that will do something about it. Dropped as soon as any experiment has
    # reached a report: by then the situation speaks for itself.
    adoption = adoption_mod.read(root)
    if adoption is not None and adoption.is_adoption and not _anything_reported(root):
        lines += adoption_mod.brief_lines(adoption, root)

    # A Planner with a memory opens with it: everything it learned in earlier
    # experiments, so the hour spent learning this project is paid once.
    registered = planner_of(experiment) or {}
    if registered.get("planner"):
        with contextlib.suppress(planners_mod.PlannerError):
            lines += planners_mod.brief_lines(
                planners_mod.load(registered["planner"], root), experiment.name
            )

    # Before the state and long before any plan: what this project already
    # decided. A Planner that reads the wrong document plans against the wrong
    # facts, and it has no way to know which document is which unless told.
    try:
        lines += project_mod.brief_lines(project_mod.load_project_context(exp_root), exp_root)
    except project_mod.ProjectError as exc:
        lines += ["## Project context", "", f"> Could not be read: {exc}", ""]

    lines += ["## State", "", f"**{state.state}** — {state.detail}", ""]

    if experiment.plan_path.is_file() and experiment.question:
        try:
            plan = load_plan(experiment.plan_path)
        except PlanError:
            pass
        else:
            board = {t.id: t for t in load_board(exp_root / "tasks")}
            lines += [
                f"Goal: {plan.goal.strip()}",
                f"Integration spec: {plan.integration or '(none declared)'}",
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

    if not _planner_model(experiment, root):
        # A hand-appointed Planner is the one agent the harness cannot inspect.
        # If it does not say what it is, the report cannot either.
        lines += [
            "## Register yourself first",
            "",
            "This experiment has no Planner model on record, so its report cannot be",
            "compared with any other. You were opened by a person; say what you are:",
            "",
            "```bash",
            f"python -m harness planner brief {name} --register planner \\",
            "  --model <the model you are running on> --effort <low|medium|high>",
            "```",
            "",
        ]

    lines += [
        "## Next",
        "",
        "```bash",
        f"cd {exp_root}",
        state.next_command,
        "```",
        "",
        "## Your role",
        "",
        "Read agents/planner.md and follow it. You own this experiment end to end:",
        "settle the question, decompose it into modules, hand each to a Worker,",
        "verify, and report back. You never write module code, and you never",
        "merge — merging is the researcher's decision.",
        "",
        f"- Worktree: {exp_root}",
        f"- Branch:   {experiment.branch}",
        f"- Plan:     {experiment.plan_path}",
        f"- Contract: {exp_root / 'agents' / 'planner.md'}",
        "",
        "## The whole sequence",
        "",
        "```bash",
        f'python -m harness exp question {experiment.name} --set "..."  # once agreed',
        f"python -m harness plan validate plans/{experiment.name}.yaml",
        f"python -m harness plan materialize plans/{experiment.name}.yaml",
        "python -m harness task list                 # what is ready",
        f"python -m harness plan run plans/{experiment.name}.yaml       # Workers build it",
        f"python -m harness exp report {experiment.name} --determinism --save",
        "```",
        "",
        "`plan run` invokes the configured Worker per module, verifies acceptance",
        "and declared deliverables, and retries with the real failure output up to",
        "the cap. A module that stays blocked is usually a brief that is wrong,",
        "not a Worker that is bad.",
        "",
        "Re-run this briefing at any time for current state — it reads real state,",
        "so it never goes stale. `harness status` gives the short version.",
        "",
        "## When you are done",
        "",
        f"Run `harness exp report {experiment.name}`. It exits non-zero until the",
        "experiment is genuinely merge-ready. Then stop and hand back to the",
        "researcher — do not merge.",
        "",
    ]
    return "\n".join(lines)


def register_planner(
    name: str,
    label: str,
    root: str | Path = ".",
    model: str = "",
    effort: str = "",
    require_model: bool = False,
) -> Path:
    """Record who is driving an experiment, and on what.

    The harness cannot choose the Planner's model — that session was opened by
    a person — but recording it makes the tier split auditable: a report can
    then say which tier ran what, instead of the split being an intention
    nobody can check.

    ``require_model`` is set when a person registers a session by hand, which
    is the one case where the harness has no other way to learn the answer. A
    spawned Planner may legitimately have no model of its own — its command can
    pin one — so that path records whatever it knows and the report flags the
    gap instead of refusing.
    """
    if require_model and not str(model).strip():
        raise ExperimentError(
            "registering a Planner requires --model: a run whose Planner model is "
            "unknown cannot be compared with any other run. Pass the model the "
            "session is actually running on."
        )
    experiment = find_experiment(name, root)
    marker = experiment.path / "results" / "experiments" / experiment.name / "planner.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "experiment": experiment.name,
                "planner": label,
                "model": model,
                "effort": effort,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return marker


def planner_of(experiment: Experiment) -> dict[str, Any] | None:
    """The registered Planner for an experiment, with its declared tier."""
    marker = experiment.path / "results" / "experiments" / experiment.name / "planner.json"
    if not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
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


# ---------------------------------------------------------------------------
# Orientation: where am I, and what do I do next?


@dataclasses.dataclass
class ExperimentState:
    """One experiment's position in the flow, derived from files alone."""

    name: str
    branch: str
    state: str
    detail: str
    next_command: str


@dataclasses.dataclass
class ProjectStatus:
    """What a newcomer needs to know on arrival, read from real state."""

    instantiated: bool
    project_name: str
    demo_present: bool
    worker_adapter: str = "manual"
    worker_tier: dict[str, Any] = dataclasses.field(default_factory=dict)
    planner_tier: dict[str, Any] = dataclasses.field(default_factory=dict)
    experiments: list[ExperimentState] = dataclasses.field(default_factory=list)
    here: str | None = None
    headline: str = ""
    next_steps: list[str] = dataclasses.field(default_factory=list)
    #: False when 'manual' is a fallback rather than a choice — the difference
    #: between "nothing to spawn" and "nothing configured to spawn with".
    agents_config_found: bool = True
    agents_config_path: str = ""


def experiment_state(experiment: Experiment) -> ExperimentState:
    """Classify an experiment without running anything.

    Cheap on purpose: orientation must be instant, so this reads the plan and
    the board and never executes a spec.
    """
    name, branch = experiment.name, experiment.branch

    def state(kind: str, detail: str, command: str) -> ExperimentState:
        return ExperimentState(name, branch, kind, detail, command)

    if not experiment.question:
        return state(
            "question unsettled",
            "the question has not been agreed with the researcher yet",
            f'harness exp question {name} --set "<their question, verbatim>"',
        )
    if not experiment.plan_path.is_file():
        return state(
            "no plan",
            "the Planner has not written a plan yet",
            f"harness plan validate plans/{name}.yaml   # after writing it",
        )
    try:
        plan = load_plan(experiment.plan_path)
    except PlanError as exc:
        kind = "scaffold" if "still the scaffold" in str(exc) else "invalid plan"
        detail = (
            "the plan is still all TODOs" if kind == "scaffold" else f"the plan is invalid: {exc}"
        )
        hint = "after replacing every TODO in it" if kind == "scaffold" else "after fixing it"
        return state(kind, detail, f"harness plan validate plans/{name}.yaml   # {hint}")

    board = {t.id: t for t in load_board(get_tasks_dir(experiment.path))}
    module_ids = [m.id for m in plan.modules]
    present = [i for i in module_ids if i in board]
    if len(present) < len(module_ids):
        return state(
            "not materialized",
            f"{len(module_ids) - len(present)} module(s) have no task file",
            f"harness plan materialize plans/{name}.yaml",
        )

    blocked = [i for i in module_ids if board[i].status == "blocked"]
    if blocked:
        return state(
            "blocked",
            f"worker gave up on {blocked} — the brief or contract needs the Planner",
            f"harness task show --id {blocked[0]}",
        )

    done = [i for i in module_ids if board[i].is_done]
    if len(done) < len(module_ids):
        return state(
            "building",
            f"{len(done)}/{len(module_ids)} module(s) done",
            f"harness plan run plans/{name}.yaml",
        )
    return state(
        "ready to report",
        "every module is done",
        f"harness exp report {name} --determinism --save",
    )


def project_status(root: str | Path = ".", cwd: str | Path | None = None) -> ProjectStatus:
    """Answer 'where am I and what now?' from the repository's actual state."""
    root = Path(root).resolve()
    pyproject = root / "pyproject.toml"
    project_name = "unknown"
    if pyproject.is_file():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.startswith("name ="):
                project_name = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    instantiated = get_agents_config_path(root).is_file()
    demo_present = (get_plans_dir(root) / "demo-pipeline.yaml").is_file()

    try:
        experiments = [experiment_state(e) for e in list_experiments(root)]
    except ExperimentError:
        experiments = []

    here = None
    current = Path(cwd or Path.cwd()).resolve()
    for candidate in list_experiments(root) if experiments else []:
        if current == candidate.path or candidate.path in current.parents:
            here = candidate.name
            break

    status = ProjectStatus(
        instantiated=instantiated,
        project_name=project_name,
        demo_present=demo_present,
        experiments=experiments,
        here=here,
        worker_adapter=_worker_adapter(root),
        worker_tier=_agent_tier(root, "worker"),
        planner_tier=_agent_tier(root, "planner"),
        agents_config_found=instantiated,
        agents_config_path=str(get_agents_config_path(root)),
    )

    if not instantiated:
        status.headline = "Project not initialized yet — run 'harness init' to scaffold it."
        status.next_steps = [
            "harness init   # scaffold project files and configure agents",
        ]
        return status

    if not experiments:
        status.headline = f"'{project_name}' is set up, with no experiments yet."
        status.next_steps = [
            "harness exp start <hypothesis>            # a branch + worktree for one question",
            "harness planner brief <hypothesis> --register <label>"
            "   # give this to an agent session",
        ]
        return status

    unfinished = [e for e in experiments if e.state != "ready to report"]
    focus = next((e for e in experiments if e.name == status.here), None) or (
        unfinished[0] if unfinished else experiments[0]
    )
    where = f"in experiment '{status.here}'" if status.here else "at the project root"
    status.headline = f"You are {where}; {len(experiments)} experiment(s) in flight."
    status.next_steps = [focus.next_command]
    if focus.state == "ready to report":
        status.next_steps.append(f"git merge {focus.branch}    # only after you read the report")
    return status


def _agent_tier(root: Path, section: str = "worker") -> dict[str, Any]:
    """How one tier is configured to run, for display and for the report."""
    from harness.worker import WorkerError, load_agent_config

    try:
        config = load_agent_config(section, root=root)
    except WorkerError as exc:
        return {"adapter": "misconfigured", "detail": str(exc)}
    return {
        "adapter": config.adapter,
        "platform": config.platform,
        "model": config.model,
        "effort": config.effort,
        "session": config.session,
    }


def _worker_adapter(root: Path) -> str:
    """Which Worker adapter is configured — 'manual' means no Workers are spawned."""
    from harness.worker import WorkerError, load_worker_config

    try:
        return load_worker_config(root=root).adapter
    except WorkerError:
        return "misconfigured"


# ---------------------------------------------------------------------------
# Spawning a Planner (Tier 1 -> Tier 2)


@dataclasses.dataclass
class PlannerAttempt:
    number: int
    exit_code: int | None = None
    duration_s: float = 0.0
    state: str = ""
    detail: str = ""


@dataclasses.dataclass
class PlannerOutcome:
    """The result of driving one experiment's Planner to a reportable state."""

    experiment: str
    adapter: str
    status: str = "pending"  # ready | incomplete | needs_human | error
    platform: str = ""
    model: str = ""
    effort: str = ""
    attempts: list[PlannerAttempt] = dataclasses.field(default_factory=list)
    brief_path: str | None = None
    message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "ready"


def run_planner(
    name: str,
    config: Any | None = None,
    root: str | Path = ".",
) -> PlannerOutcome:
    """Invoke a Planner on an experiment until it is reportable, or give up.

    A Worker's definition of done is its acceptance; a Planner's is the
    experiment reaching "ready to report" — every module built and passing.
    Same loop, one altitude up, and the same reason for it: the loop belongs in
    tested code, not in whatever agent happens to be driving.
    """
    import time

    from harness.worker import AgentConfig, invoke_agent

    config = config or AgentConfig(label="planner")
    experiment = find_experiment(name, root)
    outcome = PlannerOutcome(
        experiment=experiment.name,
        adapter=config.adapter,
        platform=config.platform,
        model=config.model,
        effort=config.effort,
    )

    brief_dir = experiment.path / "results" / "experiments" / experiment.name
    brief_dir.mkdir(parents=True, exist_ok=True)
    brief_path = brief_dir / "planner-brief.md"
    brief_path.write_text(planner_brief(name, root), encoding="utf-8")
    outcome.brief_path = str(brief_path)

    if config.adapter == "manual":
        outcome.status = "needs_human"
        outcome.message = (
            f"briefing written to {brief_path}. Open a session, tell it to run "
            f"`harness planner brief {name} --register <label>`, and follow it."
        )
        return outcome

    if not experiment.question:
        # A spawned Planner cannot ask what is wanted; it would invent a goal
        # and build something nobody requested. Interactively that conversation
        # is exactly the right move, so point there instead of guessing.
        outcome.status = "needs_human"
        outcome.message = (
            f"experiment '{name}' has no recorded question, so there is nothing "
            "to spawn a Planner for. Either record one:\n"
            f'    harness exp question {name} --set "..."\n'
            "or drive it interactively — open a session and tell it to run "
            f"`harness planner brief {name} --register <label>`, agree on the "
            "question together, and it will record it for you."
        )
        return outcome

    register_planner(name, config.label, root, model=config.model, effort=config.effort)

    for number in range(1, config.attempts + 1):
        attempt = PlannerAttempt(number=number)
        started = time.monotonic()
        prompt = planner_brief(name, root)
        brief_path.write_text(prompt, encoding="utf-8")
        try:
            proc = invoke_agent(
                config,
                experiment.path,
                prompt,
                brief_path,
                first_attempt=number == 1,
                experiment=experiment.name,
            )
            attempt.exit_code = proc.returncode
            (brief_dir / f"planner-attempt-{number:02d}.log").write_text(
                f"exit={proc.returncode}\n\n## stdout\n{proc.stdout}\n\n## stderr\n{proc.stderr}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            attempt.duration_s = time.monotonic() - started
            attempt.detail = f"could not invoke planner: {exc}"
            outcome.attempts.append(attempt)
            outcome.status = "error"
            outcome.message = attempt.detail
            return outcome
        except Exception as exc:  # timeout and friends
            attempt.detail = f"planner invocation failed: {exc}"
        attempt.duration_s = time.monotonic() - started

        state = experiment_state(find_experiment(name, root))
        attempt.state = state.state
        attempt.detail = attempt.detail or state.detail
        outcome.attempts.append(attempt)
        if state.state == "ready to report":
            outcome.status = "ready"
            outcome.message = f"experiment '{name}' is ready to report"
            return outcome

    outcome.status = "incomplete"
    last = outcome.attempts[-1].state if outcome.attempts else "unknown"
    outcome.message = (
        f"experiment '{name}' is still '{last}' after {config.attempts} attempt(s) — "
        "a researcher should look at it"
    )
    return outcome
