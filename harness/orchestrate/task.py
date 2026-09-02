"""Worker tasks — self-contained work orders materialized from plans.

A task file (``tasks/<id>.task.yaml``) is the *only* thing a Worker agent
needs besides the repository itself: it carries the module brief, the IO
contract, constraints, deliverables, and a checklist of named tests
criteria. Lifecycle state (``todo → in_progress → done``) lives inside the
task file so agents can coordinate through git.

Schema reference: ``docs/orchestration.md``.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from harness.orchestrate.plan import Contract, Module, Plan, normalize_executor
from harness.verify.checklist import ChecklistRun, Item, ItemResult, parse_list, run_items
from harness.verify.report import write_reports

TASK_STATUSES = ("todo", "in_progress", "done", "blocked")


class TaskError(ValueError):
    """Raised when a task is missing, invalid, or in the wrong state."""


def _str_presenter(dumper: yaml.Dumper, data: str) -> yaml.Node:
    if "\n" in data:  # keep multiline briefs readable
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.SafeDumper.add_representer(str, _str_presenter)


@dataclasses.dataclass
class Task:
    """A single Worker work order with lifecycle state."""

    id: str
    plan: str
    title: str
    brief: str
    depends_on: list[str] = dataclasses.field(default_factory=list)
    deliverables: list[str] = dataclasses.field(default_factory=list)
    constraints: list[str] = dataclasses.field(default_factory=list)
    contract: Contract = dataclasses.field(default_factory=Contract)
    checklist: list[Item] = dataclasses.field(default_factory=list)
    #: Defaults to the Main Worker, like the module it came from.
    executor: str = "main"
    status: str = "todo"
    worker: str | None = None
    log: list[str] = dataclasses.field(default_factory=list)
    path: Path | None = None

    @property
    def is_done(self) -> bool:
        return self.status == "done"

    @property
    def is_ready(self) -> bool:
        return self.status == "todo"


# ---------------------------------------------------------------------------
# (De)serialization


def _ports_to_list(ports: list[Any]) -> list[dict[str, str]]:
    return [{"name": p.name, "type": p.type, "description": p.description} for p in ports]


def task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "task": {
            "id": task.id,
            "plan": task.plan,
            "title": task.title,
            "depends_on": list(task.depends_on),
            "brief": task.brief,
            "contract": {
                "inputs": _ports_to_list(task.contract.inputs),
                "outputs": _ports_to_list(task.contract.outputs),
            },
            "deliverables": list(task.deliverables),
            "constraints": list(task.constraints),
            "checklist": [item.to_dict() for item in task.checklist],
            "executor": task.executor,
            "status": task.status,
            "worker": task.worker,
            "log": list(task.log),
        }
    }


def _task_from_dict(data: dict[str, Any], path: Path | None = None) -> Task:
    if "task" not in data or not isinstance(data["task"], dict):
        raise TaskError(f"task file must have a top-level 'task:' mapping: {path}")
    entry = data["task"]
    task_id = entry.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise TaskError(f"task requires a non-empty 'id': {path}")
    status = entry.get("status", "todo")
    if status not in TASK_STATUSES:
        raise TaskError(
            f"task '{task_id}': invalid status '{status}'. valid: {list(TASK_STATUSES)}"
        )
    checklist = parse_list(entry.get("checklist"), module=str(entry.get("id", "")))
    return Task(
        id=task_id,
        plan=str(entry.get("plan", "")),
        title=str(entry.get("title", task_id)),
        depends_on=[str(d) for d in entry.get("depends_on", [])],
        brief=str(entry.get("brief", "")),
        deliverables=[str(d) for d in entry.get("deliverables", [])],
        constraints=[str(c) for c in entry.get("constraints", [])],
        contract=Contract.from_dict(entry.get("contract")),
        checklist=checklist,
        executor=normalize_executor(str(entry.get("executor", "main"))) or "main",
        status=status,
        worker=entry.get("worker"),
        log=[str(line) for line in entry.get("log", [])],
        path=path,
    )


# ---------------------------------------------------------------------------
# Storage


def task_path(tasks_dir: str | Path, task_id: str) -> Path:
    return Path(tasks_dir) / f"{task_id}.task.yaml"


def load_task(tasks_dir: str | Path, task_id: str) -> Task:
    path = task_path(tasks_dir, task_id)
    if not path.is_file():
        raise TaskError(f"task file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaskError(f"invalid YAML in {path}: {exc}") from exc
    return _task_from_dict(raw or {}, path)


def save_task(task: Task) -> Path:
    if task.path is None:
        raise TaskError(f"task '{task.id}' has no backing file path")
    task.path.parent.mkdir(parents=True, exist_ok=True)
    task.path.write_text(
        yaml.safe_dump(
            task_to_dict(task), sort_keys=False, allow_unicode=True, default_flow_style=False
        ),
        encoding="utf-8",
    )
    return task.path


def load_board(tasks_dir: str | Path) -> list[Task]:
    """Load every task file in ``tasks_dir``, sorted by id."""
    directory = Path(tasks_dir)
    if not directory.is_dir():
        return []
    tasks = []
    for path in sorted(directory.glob("*.task.yaml")):
        tasks.append(_task_from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path))
    return tasks


# ---------------------------------------------------------------------------
# Materialization (Planner → tasks)


def task_from_module(plan: Plan, module: Module) -> Task:
    """Build a self-contained Task from a plan module."""
    return Task(
        id=module.id,
        plan=plan.name,
        title=module.title,
        depends_on=list(module.depends_on),
        brief=module.brief,
        deliverables=list(module.deliverables),
        constraints=list(module.constraints),
        contract=module.contract,
        checklist=list(module.checklist),
        executor=module.executor,
    )


def materialize(plan: Plan, tasks_dir: str | Path, force: bool = False) -> list[Path]:
    """Write one task file per plan module.

    Existing task files are left untouched unless ``force`` is set. With
    ``force``, the module's *specification* (title, brief, contract,
    deliverables, constraints, checklist, deps) is refreshed from the plan
    while the *lifecycle* (``status``, ``worker``, ``log``) is preserved — a
    re-materialization must never erase the board or the audit trail. The
    refresh itself is appended to the log.
    """
    written: list[Path] = []
    for module_id in plan.topological_order():
        path = task_path(tasks_dir, module_id)
        existing: Task | None = None
        if path.exists():
            if not force:
                continue
            existing = load_task(tasks_dir, module_id)
        task = task_from_module(plan, plan.module(module_id))
        task.path = path
        if existing is not None:
            task.status = existing.status
            task.worker = existing.worker
            task.log = list(existing.log)
            task.log.append(f"{_now()} re-materialized from plan '{plan.name}' (--force)")
        written.append(save_task(task))
    return written


#: Task fields owned by the plan; the rest (status/worker/log) is lifecycle.
SPEC_FIELDS = (
    "title",
    "depends_on",
    "brief",
    "contract",
    "deliverables",
    "constraints",
    "checklist",
)


def spec_drift(plan: Plan, tasks_dir: str | Path) -> dict[str, list[str]]:
    """Map module id -> plan-owned fields that differ from its materialized task.

    ``materialize`` skips existing files, so editing a plan leaves task files
    stale and Workers reading instructions the Planner has already replaced.
    This makes that divergence visible (and CI-gateable) instead of silent.
    """
    drift: dict[str, list[str]] = {}
    for module in plan.modules:
        path = task_path(tasks_dir, module.id)
        if not path.exists():
            drift[module.id] = ["(not materialized)"]
            continue
        stored = task_to_dict(load_task(tasks_dir, module.id))["task"]
        expected = task_to_dict(task_from_module(plan, module))["task"]
        stale = [f for f in SPEC_FIELDS if stored.get(f) != expected.get(f)]
        if stale:
            drift[module.id] = stale
    return drift


# ---------------------------------------------------------------------------
# Lifecycle


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def blocking_dependencies(tasks_dir: str | Path, task: Task) -> list[str]:
    """Dependency ids of ``task`` that are not 'done' (or do not exist yet)."""
    board = {t.id: t for t in load_board(tasks_dir)}
    return [d for d in task.depends_on if d not in board or board[d].status != "done"]


def claim(tasks_dir: str | Path, task_id: str, worker: str, force: bool = False) -> Task:
    task = load_task(tasks_dir, task_id)
    if task.status not in ("todo", "blocked"):
        raise TaskError(
            f"task '{task_id}' is '{task.status}'"
            + (f" (worker: {task.worker})" if task.worker else "")
            + " — only 'todo' or 'blocked' tasks can be claimed"
        )
    pending = blocking_dependencies(tasks_dir, task)
    if pending and not force:
        raise TaskError(
            f"task '{task_id}' is not ready: dependencies not done: {pending}. "
            "Finish them first, or pass --force to claim anyway."
        )
    task.status = "in_progress"
    task.worker = worker
    note = f" (--force, deps pending: {pending})" if pending else ""
    task.log.append(f"{_now()} claimed by {worker}{note}")
    save_task(task)
    return task


def block(tasks_dir: str | Path, task_id: str, reason: str) -> Task:
    task = load_task(tasks_dir, task_id)
    if task.status == "done":
        raise TaskError(f"task '{task_id}' is already done")
    task.status = "blocked"
    task.log.append(f"{_now()} blocked: {reason}")
    save_task(task)
    return task


def _deliverables_result(task: Task, root: Path) -> ItemResult:
    """The one assertion the harness still makes itself: the files are there.

    Deliverables are part of the contract, not a test of behaviour, which is why
    they survived the removal of the built-in check types: a task whose whole
    checklist passes but whose declared files are missing has not delivered, and
    no project-specific test would necessarily notice.
    """
    missing = [
        rel
        for rel in task.deliverables
        if not (Path(rel) if Path(rel).is_absolute() else root / rel).exists()
    ]
    return ItemResult(
        module=task.id,
        name="deliverables",
        command=(
            f"(harness: declared deliverables missing: {', '.join(missing)})"
            if missing
            else "(harness: declared deliverables exist)"
        ),
        exit_code=1 if missing else 0,
        duration_s=0.0,
        log_path="",
    )


def verify_task(
    task: Task,
    root: str | Path = ".",
    results_dir: str | Path = "results",
    progress=None,
) -> ChecklistRun:
    """Run this task's checklist, then assert its declared deliverables exist."""
    run = run_items(
        list(task.checklist),
        root=root,
        results_dir=results_dir,
        scope=task.id,
        progress=progress,
    )
    if task.deliverables:
        run.results.append(_deliverables_result(task, Path(root).resolve()))
    write_reports(run)
    return run


def complete(
    tasks_dir: str | Path,
    task_id: str,
    worker: str | None = None,
    root: str | Path = ".",
    results_dir: str | Path = "results",
) -> tuple[Task, ChecklistRun]:
    """Run the checklist and the deliverables gate, then mark the task done."""
    task = load_task(tasks_dir, task_id)
    result = verify_task(task, root=root, results_dir=results_dir)
    if not result.passed:
        return task, result
    task.status = "done"
    if worker:
        task.worker = worker
    task.log.append(f"{_now()} checklist passed ({len(result.results)} item(s)) — done")
    save_task(task)
    return task, result


def ready_task_ids(board: list[Task]) -> list[str]:
    """Task ids that are 'todo' with all dependencies 'done'."""
    by_id = {t.id: t for t in board}
    ready = []
    for task in board:
        if task.status != "todo":
            continue
        deps_done = all(dep in by_id and by_id[dep].status == "done" for dep in task.depends_on)
        if deps_done:
            ready.append(task.id)
    return ready
