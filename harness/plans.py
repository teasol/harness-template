"""Plans in flight — one piece of work, isolated, from start to report.

A plan is a piece of work the Planner takes on: a series of module tasks, on its
own git branch, in its own worktree. You and the Planner agree what it is by
talking; the harness does not make you write that down before it will let you
start.

This used to be a second concept called a *branch*, sitting on top of the plan
and one-to-one with it — one name for the work, another for the file describing
it, and a third meaning for the word git already owns. So the two are one: the
plan is the unit of work, and the git branch and worktree are where it happens.
:mod:`harness.plan` is the plan document (modules, contracts, report); this
module is a plan in flight (its worktree, its state, its report).

Worktrees, not just git branches, because several plans run at once and each
needs its own files on disk. Within one plan Sub-Workers run sequentially, so
its task board stays coherent and dependency gates read current state.

A report is deliberately self-contained: it draws only on its own plan's
artifacts. Comparing two plans is your job, done by reading finished reports —
not something one plan may reach across to do.
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
from harness import invocation
from harness import plan as plan_mod
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

#: Where worktrees live. A plan is identified by *having* a worktree here, not
#: by a name prefix — its git branch is called whatever you called the plan.
DEFAULT_WORKTREE_ROOT = ".worktrees"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: Names git or the project already means something by.
RESERVED_NAMES = frozenset({"main", "master", "head", "origin"})


class WorkPlanError(RuntimeError):
    """Raised when a plan cannot be started, found, or reported on."""


@dataclasses.dataclass
class WorkPlan:
    """One plan in flight: its git branch, its worktree, its plan file."""

    name: str
    git_branch: str
    path: Path
    head: str = ""

    @property
    def plan_path(self) -> Path:
        return get_plans_dir(self.path) / f"{self.name}.yaml"


@dataclasses.dataclass
class MetricValue:
    """A number the harness extracted, plus where it came from."""

    name: str
    value: Any = None
    source: str = ""
    metric: str = ""
    error: str | None = None


@dataclasses.dataclass
class PlanReport:
    """What the researcher reads to decide whether to merge."""

    name: str
    git_branch: str
    goal: str
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
        raise WorkPlanError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def list_plans(
    root: str | Path = ".", worktree_root: str | Path = DEFAULT_WORKTREE_ROOT
) -> list[WorkPlan]:
    """Every harness worktree git knows about, sorted by name.

    Membership is decided by *where the worktree is*, not by a prefix on the
    plan name. Plans are named whatever you called them, so there is
    nothing in the name to match on — and a prefix would be a second naming
    scheme to remember for no gain.
    """
    root = Path(root).resolve()
    base = Path(worktree_root)
    base = base if base.is_absolute() else root / base
    porcelain = _git(root, "worktree", "list", "--porcelain")
    found: list[WorkPlan] = []
    path: str | None = None
    head = ""
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            path, head = line[len("worktree ") :], ""
        elif line.startswith("HEAD "):
            head = line[len("HEAD ") :]
        elif line.startswith("branch ") and path is not None:
            name_ref = line[len("branch ") :].removeprefix("refs/heads/")
            resolved = Path(path).resolve()
            if resolved.parent == base:
                found.append(
                    WorkPlan(name=resolved.name, git_branch=name_ref, path=resolved, head=head)
                )
            path = None
    return sorted(found, key=lambda p: p.name)


def resolve_plan_path(value: str, root: str | Path = ".") -> Path:
    """Accept either a plan's name or a path to its YAML, and return the path.

    Plans are addressed by name now that a plan *is* the unit of work — the
    thing you start, approve, run and report on. Paths keep working, because a
    Planner working inside its own worktree naturally has one, and because the
    demo plan is a file that no worktree owns.
    """
    candidate = Path(value)
    if candidate.suffix in {".yaml", ".yml"} or candidate.exists():
        return candidate
    with contextlib.suppress(WorkPlanError):
        return find_plan(value, root).plan_path
    return get_plans_dir(root) / f"{value}.yaml"


def find_plan(name: str, root: str | Path = ".") -> WorkPlan:
    for candidate in list_plans(root):
        if candidate.name == name:
            return candidate
    known = [b.name for b in list_plans(root)]
    raise WorkPlanError(f"no plan '{name}'. known: {known or '(none)'}")


# ---------------------------------------------------------------------------
# lifecycle


PLAN_TEMPLATE = """\
# Plan '{name}' — written by the Planner, read by Workers.
#
# TODO(Planner): replace every TODO below, then run:
#   {prefix} plan validate {name}
#   {prefix} plan materialize {name}
plan:
  name: {name}
  goal: >
    TODO: one paragraph stating what this work is meant to establish. This is
    what the report answers to, so write it as you would explain it out loud.

  # What to report back. Declare WHERE each number lives; the harness extracts
  # the value from the real artifact. Paths must stay inside this plan — a
  # report may not read another plan's results.
  report:
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
    """Copy the project's agent configuration into a new plan's worktree.

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
) -> WorkPlan:
    """Start a plan: a git branch and a worktree to do one piece of work in."""
    if not NAME_RE.match(name):
        raise WorkPlanError(
            f"invalid plan name '{name}' — use lowercase letters, digits, and hyphens"
        )
    if name.lower() in RESERVED_NAMES:
        raise WorkPlanError(f"'{name}' is reserved — pick a name for the work itself")
    root = Path(root).resolve()
    git_branch = name
    worktree_root = Path(worktree_root)
    path = worktree_root if worktree_root.is_absolute() else root / worktree_root
    path = path / name
    if path.exists():
        raise WorkPlanError(f"path already exists: {path}")
    existing = _git(root, "branch", "--list", git_branch)
    if existing:
        raise WorkPlanError(f"git branch '{git_branch}' already exists — pick another name")

    path.parent.mkdir(parents=True, exist_ok=True)
    _git(root, "worktree", "add", "-b", git_branch, str(path), base)

    created = WorkPlan(
        name=name, git_branch=git_branch, path=path, head=_git(path, "rev-parse", "HEAD")
    )
    _inherit_agent_configs(root, path)
    if scaffold:
        if not created.plan_path.exists():
            created.plan_path.parent.mkdir(parents=True, exist_ok=True)
            created.plan_path.write_text(
                PLAN_TEMPLATE.format(name=name, prefix=invocation.command_prefix()),
                encoding="utf-8",
            )
        # Scaffold the integration spec the plan points at, so the Planner's
        # first validation error is about the TODOs it must fill in, not about
        # a file the scaffold neglected to create.
        spec_path = get_configs_dir(path) / f"{name}.yaml"
        if not spec_path.exists():
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(INTEGRATION_TEMPLATE.format(name=name), encoding="utf-8")
    return created


def remove(name: str, root: str | Path = ".", force: bool = False) -> WorkPlan:
    """Remove a plan's worktree. The git branch is kept — it is the record."""
    work = find_plan(name, root)
    args = ["worktree", "remove", str(work.path)]
    if force:
        args.append("--force")
    _git(root, *args)
    return work


# ---------------------------------------------------------------------------
# reporting


def _resolve_plan(work: WorkPlan) -> Plan:
    if not work.plan_path.is_file():
        raise WorkPlanError(
            f"plan '{work.name}' has no plan file at {work.plan_path}. "
            "The Planner writes it before anything can be reported."
        )
    try:
        return load_plan(work.plan_path)
    except PlanError as exc:
        raise WorkPlanError(f"plan for '{work.name}' is invalid: {exc}") from exc


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
) -> PlanReport:
    """Assemble the researcher's decision aid for one plan.

    The spine — integration result, task acceptance, determinism, the commit
    to merge — is measured here, not narrated by an agent. The requested
    metrics are extracted from the artifacts the run produced.
    """
    work = find_plan(name, root)
    plan = _resolve_plan(work)
    plan_root = work.path

    report = PlanReport(
        name=work.name,
        git_branch=work.git_branch,
        goal=plan.goal.strip(),
        merge_ready=False,
        artifacts=list(plan.report.artifacts),
        provenance=collect_provenance(plan_root, seed=None),
    )
    report.commit = report.provenance.get("git_commit")
    report.dirty = report.provenance.get("git_dirty")
    report.tiers = {
        "planner": planner_of(work) or _agent_tier(plan_root, "planner"),
        "worker": _agent_tier(plan_root, "worker"),
    }

    # --- task board: is every module actually finished, and still passing? ---
    # Scoped to *this plan's* modules. A tasks/ directory may hold task files
    # from other plans (the shipped demo, an earlier plan); counting those
    # would report a plan as complete when none of its own modules
    # were built — and that number decides a merge.
    board = {t.id: t for t in load_board(plan_root / "tasks")}
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
            result = verify_task(task, root=plan_root, results_dir=plan_root / "results")
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
        spec_path = _integration_path(plan_root, plan.integration)
        try:
            spec = load_spec(spec_path)
        except SpecError as exc:
            report.integration = f"spec error: {exc}"
        else:
            runner = Runner(root=plan_root, results_dir=plan_root / "results")
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
        previous = _last_run(plan_root, plan.integration)
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
                load_spec(_integration_path(plan_root, plan.integration)),
                times=2,
                root=plan_root,
                results_dir=plan_root / "results" / "reproduce",
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

    # The approval gate exists so that somebody other than the author saw the
    # plan before the budget was spent. A Planner that approves its own plan
    # passes the check and defeats the purpose, so the report says so.
    approver = plan_mod.approved_by(work.plan_path)
    planner_label = str((report.tiers.get("planner") or {}).get("planner") or "") or (
        (planner_of(work) or {}).get("planner") or ""
    )
    if (
        approver
        and planner_label
        and approver.strip().lower() == str(planner_label).strip().lower()
    ):
        report.caveats.append(
            f"the plan was approved by '{approver}', which is the Planner itself — "
            "nobody else saw it before the work started"
        )

    # A result whose Planner model is unknown cannot be compared with any other
    # result, so the gap is stated rather than left to be noticed.
    if not ((report.tiers.get("planner") or {}).get("model") or _planner_model(work, root)):
        report.caveats.append(
            "Planner model not recorded — this run cannot be compared with another. "
            "Record it on the Planner with `harness planner set <planner> --model <model>`, "
            f"or on this plan alone with `harness planner brief {name} "
            "--register <label> --model <model>`."
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
        blockers.append("the plan is not reproducible")

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


def _last_run(plan_root: Path, integration: str | None) -> PreviousRun | None:
    """The most recent completed run of this plan's integration spec.

    Matched on the spec's name, so an unrelated spec's run in the same results
    directory is never mistaken for this plan's evidence.
    """
    if integration is None:
        return None
    try:
        spec_name = load_spec(_integration_path(plan_root, integration)).name
    except (SpecError, WorkPlanError, OSError):
        return None
    runs_dir = plan_root / "results" / "runs"
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


def _integration_path(plan_root: Path, integration: str) -> Path:
    candidate = Path(integration)
    return candidate if candidate.is_absolute() else plan_root / candidate


# ---------------------------------------------------------------------------
# rendering


def report_markdown(report: PlanReport) -> str:
    lines = [
        f"# Plan report: {report.name}",
        "",
    ]
    if report.goal:
        lines += ["## Goal", "", report.goal, ""]

    verdict = "READY TO MERGE" if report.merge_ready else "NOT READY"
    commit = report.commit or "unknown"
    if report.dirty:
        commit += " (dirty worktree)"
    lines += [
        "## Verdict",
        "",
        f"**{verdict}** — merging is the researcher's call; the harness only reports.",
        "",
        f"- Git branch: `{report.git_branch}`",
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


def write_plan_report(report: PlanReport, plan_root: str | Path, save: bool = False) -> list[Path]:
    """Write the report under ``results/`` and, with ``save``, into the plan."""
    plan_root = Path(plan_root)
    written: list[Path] = []

    out_dir = plan_root / "results" / "plans" / report.name
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    json_path.write_text(
        json.dumps(dataclasses.asdict(report), indent=2, default=str) + "\n", encoding="utf-8"
    )
    md_path = out_dir / "report.md"
    md_path.write_text(report_markdown(report), encoding="utf-8")
    written += [json_path, md_path]

    if save:
        saved_dir = plan_root / "plans" / report.name
        saved_dir.mkdir(parents=True, exist_ok=True)
        saved = saved_dir / "report.md"
        saved.write_text(report_markdown(report), encoding="utf-8")
        written.append(saved)
    return written


# ---------------------------------------------------------------------------
# Planner registration


def _anything_reported(root: str | Path = ".") -> bool:
    """True once any plan here has reached a reportable state.

    The adoption framing is for a project where nothing has been proven yet. Once
    one plan has, repeating it in every briefing is noise.
    """
    try:
        return any(e.state == "ready to report" for e in (plan_state(x) for x in list_plans(root)))
    except WorkPlanError:
        return False


def _planner_model(work: WorkPlan, root: str | Path = ".") -> str:
    """The model driving this plan, from the marker or the registry.

    A registered Planner carries its own model, so a plan linked to one
    is never "model not recorded" — the registry answers on its behalf.
    """
    marker = planner_of(work) or {}
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
    """The Planner's working briefing: the work, the state, and the next command.

    Always the same sections in the same order, whatever the plan's
    state — only their contents differ. A document that changes shape is a
    document you have to re-read; this one you can re-run and skim.

    Deliberately a plain command producing plain text: any agent runtime can be
    told to run it and follow the result. Tool-specific shims (a skill, a slash
    command) are thin optional wrappers around this, never a prerequisite.
    """
    work = find_plan(name, root)
    plan_root = work.path
    state = plan_state(work)

    lines = [
        f"# Planner briefing: {work.name}",
        "",
        "## The work",
        "",
    ]
    goal = ""
    if work.plan_path.is_file():
        with contextlib.suppress(PlanError):
            goal = load_plan(work.plan_path).goal.strip()
    if goal:
        lines += [
            goal,
            "",
            "That is the plan's stated goal, and the report answers to it. If the",
            "conversation has moved past it, change the goal — do not let the plan",
            "and what you are actually doing drift apart.",
        ]
    else:
        lines += [
            "No goal written yet. Work out with the user what this plan is for:",
            "what they want done, what would count as done, what they want to see at",
            "the end. Then write it as the plan's `goal` — one paragraph, in the",
            "words you would use out loud — and explain the plan before building it.",
        ]
    lines += [""]
    # A project that predates the harness needs saying so, once, to the Planner
    # that will do something about it. Dropped as soon as any plan has
    # reached a report: by then the situation speaks for itself.
    adoption = adoption_mod.read(root)
    if adoption is not None and adoption.is_adoption and not _anything_reported(root):
        lines += adoption_mod.brief_lines(adoption, root)

    # A Planner with a memory opens with it: everything it learned in earlier
    # plans, so the hour spent learning this project is paid once.
    registered = planner_of(work) or {}
    if registered.get("planner"):
        with contextlib.suppress(planners_mod.PlannerError):
            lines += planners_mod.brief_lines(
                planners_mod.load(registered["planner"], root), work.name
            )

    # Before the state and long before any plan: what this project already
    # decided. A Planner that reads the wrong document plans against the wrong
    # facts, and it has no way to know which document is which unless told.
    try:
        lines += project_mod.brief_lines(project_mod.load_project_context(plan_root), plan_root)
    except project_mod.ProjectError as exc:
        lines += ["## Project context", "", f"> Could not be read: {exc}", ""]

    lines += ["## State", "", f"**{state.state}** — {state.detail}", ""]

    if work.plan_path.is_file():
        try:
            plan = load_plan(work.plan_path)
        except PlanError:
            pass
        else:
            board = {t.id: t for t in load_board(plan_root / "tasks")}
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

    if not _planner_model(work, root):
        # A hand-appointed Planner is the one agent the harness cannot inspect.
        # If it does not say what it is, the report cannot either.
        lines += [
            "## Register yourself first",
            "",
            "This plan has no Planner model on record, so its report cannot be",
            "compared with any other. You were opened by a person; say what you are:",
            "",
            "```bash",
            f"{invocation.cmd(f'planner brief {name} --register planner')} \\",
            "  --model <the model you are running on> --effort <low|medium|high>",
            "```",
            "",
        ]

    lines += [
        "## Next",
        "",
        "```bash",
        f"cd {plan_root}",
        state.next_command,
        "```",
        "",
        "## Your role",
        "",
        "Read agents/planner.md and follow it. You own this plan end to end:",
        "agree what the work is, agree the plan, get each module",
        "built, verify, and report back.",
        "",
        "**You are also the Main Worker, so building a module yourself is a normal",
        "move, not a fallback.** Delegate to a Sub-Worker when the work is routine",
        "bulk a brief can specify completely; do it yourself when writing that brief",
        "would cost more than the work, and when a Sub-Worker has already failed on",
        "it. Either way the module keeps its contract and its acceptance — what",
        "changes is who writes the code, never whether it is checked.",
        "",
        "You never merge — that is the researcher's decision.",
        "",
        f"- Worktree: {plan_root}",
        f"- Git branch: {work.git_branch}",
        f"- Plan:     {work.plan_path}",
        f"- Contract: {plan_root / 'agents' / 'planner.md'}",
        "",
        "## The whole sequence",
        "",
        "```bash",
        *invocation.steps(
            [
                (f"plan validate {work.name}", ""),
                (
                    f"plan approve {work.name} --by <user>",
                    "they run this, after you explain it",
                ),
                (f"plan materialize {work.name}", ""),
                ("task list", "what is ready"),
                (f"plan run {work.name}", "Sub-Workers build it"),
                (f"report {work.name} --save", ""),
            ]
        ),
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
        f"Run `harness report {work.name}`. It exits non-zero until the",
        "plan is genuinely merge-ready. Then stop and hand back to the",
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
    """Record who is driving a plan, and on what.

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
        raise WorkPlanError(
            "registering a Planner requires --model: a run whose Planner model is "
            "unknown cannot be compared with any other run. Pass the model the "
            "session is actually running on."
        )
    work = find_plan(name, root)
    marker = work.path / "results" / "plans" / work.name / "planner.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "plan": work.name,
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


def planner_of(work: WorkPlan) -> dict[str, Any] | None:
    """The registered Planner for a plan, with its declared tier."""
    marker = work.path / "results" / "plans" / work.name / "planner.json"
    if not marker.is_file():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


INTEGRATION_TEMPLATE = """\
# Integration spec for plan '{name}' — verifies the ASSEMBLED whole once
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
class PlanState:
    """One plan's position in the flow, derived from files alone."""

    name: str
    git_branch: str
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
    plans: list[PlanState] = dataclasses.field(default_factory=list)
    here: str | None = None
    headline: str = ""
    next_steps: list[str] = dataclasses.field(default_factory=list)
    #: False when 'manual' is a fallback rather than a choice — the difference
    #: between "nothing to spawn" and "nothing configured to spawn with".
    agents_config_found: bool = True
    agents_config_path: str = ""


def plan_state(item: WorkPlan) -> PlanState:
    """Classify a plan without running anything.

    Cheap on purpose: orientation must be instant, so this reads the plan and
    the board and never executes a spec.
    """
    name, ref = item.name, item.git_branch

    def state(kind: str, detail: str, command: str) -> PlanState:
        # `command` is written without the prefix: what a reader has to type to
        # reach the harness depends on how it was installed, and only
        # `invocation` knows that.
        return PlanState(name, ref, kind, detail, invocation.cmd(command))

    if not item.plan_path.is_file():
        return state(
            "no plan",
            "the Planner has not written a plan yet",
            f"plan validate {name}   # after writing it",
        )
    try:
        plan = load_plan(item.plan_path)
    except PlanError as exc:
        kind = "scaffold" if "still the scaffold" in str(exc) else "invalid plan"
        detail = (
            "the plan is still all TODOs" if kind == "scaffold" else f"the plan is invalid: {exc}"
        )
        hint = "after replacing every TODO in it" if kind == "scaffold" else "after fixing it"
        return state(kind, detail, f"plan validate {name}   # {hint}")

    # A plan that nobody has agreed to is a proposal, and the state has to say
    # so — otherwise the Planner reads "materialize" as the next thing to do and
    # never explains what it intends. Explaining is the step that was missing.
    approved, why = plan_mod.approval_status(item.plan_path)
    if not approved:
        return state(
            "needs agreement",
            f"{why} — explain it to the researcher, then they approve",
            f"plan approve {name} --by <researcher>   # the researcher runs this, not you",
        )

    board = {t.id: t for t in load_board(get_tasks_dir(item.path))}
    module_ids = [m.id for m in plan.modules]
    present = [i for i in module_ids if i in board]
    if len(present) < len(module_ids):
        return state(
            "not materialized",
            f"{len(module_ids) - len(present)} module(s) have no task file",
            f"plan materialize {name}",
        )

    blocked = [i for i in module_ids if board[i].status == "blocked"]
    if blocked:
        return state(
            "blocked",
            f"a Sub-Worker gave up on {blocked} — fix the brief, or take it over "
            "yourself (`executor: main`)",
            f"task show --id {blocked[0]}",
        )

    done = [i for i in module_ids if board[i].is_done]
    if len(done) < len(module_ids):
        return state(
            "building",
            f"{len(done)}/{len(module_ids)} module(s) done",
            f"plan run {name}",
        )
    return state(
        "ready to report",
        "every module is done",
        f"report {name} --determinism --save",
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
        plans = [plan_state(e) for e in list_plans(root)]
    except WorkPlanError:
        plans = []

    here = None
    current = Path(cwd or Path.cwd()).resolve()
    for candidate in list_plans(root) if plans else []:
        if current == candidate.path or candidate.path in current.parents:
            here = candidate.name
            break

    status = ProjectStatus(
        instantiated=instantiated,
        project_name=project_name,
        demo_present=demo_present,
        plans=plans,
        here=here,
        worker_adapter=_worker_adapter(root),
        worker_tier=_agent_tier(root, "worker"),
        planner_tier=_agent_tier(root, "planner"),
        agents_config_found=instantiated,
        agents_config_path=str(get_agents_config_path(root)),
    )

    if not instantiated:
        status.headline = (
            f"Project not initialized yet — run '{invocation.cmd('init')}' to scaffold it."
        )
        status.next_steps = invocation.steps(
            [("init", "scaffold project files and configure agents")]
        )
        return status

    if not plans:
        status.headline = f"'{project_name}' is set up, with no plans yet."
        # A Planner comes before the plan it owns: registering one first
        # means the plan inherits its model — so the report is never
        # "model not recorded" — and everything that Planner has already
        # learned here. Registering afterwards works, but by then the first
        # briefing has already been written without any of it.
        from harness import planners as planners_mod

        known = [p.name for p in planners_mod.list_planners(root)]
        if known:
            # `status` is read by both the user and the Planner, and this
            # command belongs to the Planner: the user's next move is to say
            # what they want, not to name a plan. So it is labelled rather
            # than presented as the reader's own next command.
            status.next_steps = invocation.steps(
                [
                    (
                        f"plan new <name> --planner {known[0]}",
                        f"'{known[0]}' runs this, once you two agree the work",
                    )
                ]
            )
        else:
            status.next_steps = invocation.steps(
                [("create -n <planner-name>", "then tell it what you want done")]
            )
        return status

    unfinished = [e for e in plans if e.state != "ready to report"]
    focus = next((e for e in plans if e.name == status.here), None) or (
        unfinished[0] if unfinished else plans[0]
    )
    where = f"in plan '{status.here}'" if status.here else "at the project root"
    status.headline = f"You are {where}; {len(plans)} plan(s) in flight."
    status.next_steps = [focus.next_command]
    if focus.state == "ready to report":
        status.next_steps.append(
            f"git merge {focus.git_branch}    # only after you read the report"
        )
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
