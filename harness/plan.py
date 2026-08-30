"""Orchestration plans — the Planner agent's output.

A plan decomposes a goal into modules with explicit contracts (typed
inputs/outputs), dependencies forming a DAG, a worker brief per module, and
machine-checkable acceptance criteria. Plans are materialized into
self-contained task files (see :mod:`harness.task`) that Worker agents
execute one at a time, each in isolation.

A plan also declares what the branch *reports* back to the researcher
(``report:``). The researcher states what they want to see when instructing
the Planner; the Planner records **where** each number comes from, and the
harness extracts the values from real run artifacts. An agent never narrates
a result it was not made to measure.

Schema reference: ``docs/orchestration.md``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

from harness.checks import CHECK_REGISTRY
from harness.spec import SpecError, Step


class PlanError(ValueError):
    """Raised when a plan cannot be parsed or is semantically invalid."""


#: Canonical executor names, plus the older ones they replaced. The Planner is
#: the Main Worker, so "planner" and "main" are the same thing said twice.
_EXECUTORS = {
    "main": "main",
    "planner": "main",
    "self": "main",
    "sub": "sub",
    "worker": "sub",
    "delegate": "sub",
}


def normalize_executor(value: str) -> str | None:
    """Map any accepted executor spelling to ``main`` or ``sub``; None if unknown."""
    return _EXECUTORS.get(value.strip().lower())


@dataclasses.dataclass
class Port:
    """A typed input or output of a module contract."""

    name: str
    type: str
    description: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> Port:
        if not isinstance(data, dict) or "name" not in data or "type" not in data:
            raise PlanError(f"port requires 'name' and 'type', got: {data!r}")
        return cls(
            name=str(data["name"]),
            type=str(data["type"]),
            description=str(data.get("description", "")),
        )


@dataclasses.dataclass
class Contract:
    """The input/output contract of a module, enforced by its acceptance."""

    inputs: list[Port] = dataclasses.field(default_factory=list)
    outputs: list[Port] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> Contract:
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise PlanError(f"contract must be a mapping, got: {data!r}")
        for key in ("inputs", "outputs"):
            if key in data and not isinstance(data[key], list):
                raise PlanError(f"contract '{key}' must be a list")
        return cls(
            inputs=[Port.from_dict(p) for p in data.get("inputs", [])],
            outputs=[Port.from_dict(p) for p in data.get("outputs", [])],
        )


@dataclasses.dataclass
class MetricRef:
    """Where a reported number lives — never the number itself."""

    name: str
    source: str
    metric: str
    description: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> MetricRef:
        if not isinstance(data, dict):
            raise PlanError(f"report metric must be a mapping, got: {data!r}")
        for key in ("name", "source", "metric"):
            if not data.get(key):
                raise PlanError(f"report metric requires '{key}': {data!r}")
        _require_self_contained(str(data["source"]), f"report metric '{data['name']}' source")
        return cls(
            name=str(data["name"]),
            source=str(data["source"]),
            metric=str(data["metric"]),
            description=str(data.get("description", "")),
        )


@dataclasses.dataclass
class Report:
    """What this branch reports back to the researcher.

    Free-form by design: the researcher says what they want when they give
    the instruction. Self-contained by rule: a report may only draw on this
    branch's own artifacts, never another branch's, so it can be
    judged on its own terms.
    """

    metrics: list[MetricRef] = dataclasses.field(default_factory=list)
    artifacts: list[str] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> Report:
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise PlanError(f"'plan.report' must be a mapping, got: {data!r}")
        for key in ("metrics", "artifacts"):
            if key in data and not isinstance(data[key], list):
                raise PlanError(f"'plan.report.{key}' must be a list")
        artifacts = [str(a) for a in data.get("artifacts", [])]
        for artifact in artifacts:
            _require_self_contained(artifact, "report artifact")
        return cls(
            metrics=[MetricRef.from_dict(m) for m in data.get("metrics", [])],
            artifacts=artifacts,
        )


def _require_self_contained(path: str, where: str) -> None:
    """Reject a report path that could reach outside this branch.

    Comparing branches is the user's job, done by
    collecting finished reports. An branch that reads another branch's
    files cannot be judged on its own, so the harness refuses to produce one.
    """
    if path.startswith(("/", "~")):
        raise PlanError(
            f"{where} must stay inside the branch: absolute path '{path}'. "
            "Reports may only draw on this branch's own artifacts."
        )
    if ".." in Path(path).parts:
        raise PlanError(
            f"{where} must stay inside the branch: '{path}' escapes via '..'. "
            "Comparing branches is the user's job, not the plan's."
        )


@dataclasses.dataclass
class Module:
    """One unit of work in a plan: owned by exactly one Worker task."""

    id: str
    title: str
    brief: str
    #: Who builds this. ``sub`` delegates to a Sub-Worker spawned against the
    #: brief; ``main`` means the Main Worker — the Planner itself — does it
    #: directly. Delegation is for routine bulk: long mechanical coding, log
    #: parsing, anything where writing the brief costs less than doing the work
    #: twice. Core logic, planning and orchestration stay with the Main Worker.
    #: ``planner``/``worker`` are accepted as the older names for ``main``/``sub``.
    executor: str = "sub"
    depends_on: list[str] = dataclasses.field(default_factory=list)
    deliverables: list[str] = dataclasses.field(default_factory=list)
    constraints: list[str] = dataclasses.field(default_factory=list)
    contract: Contract = dataclasses.field(default_factory=Contract)
    acceptance: list[Step] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Plan:
    """A full orchestration plan: goal, module DAG, and integration spec."""

    name: str
    goal: str
    description: str = ""
    integration: str | None = None
    report: Report = dataclasses.field(default_factory=Report)
    modules: list[Module] = dataclasses.field(default_factory=list)
    source: Path | None = None

    def module(self, module_id: str) -> Module:
        for module in self.modules:
            if module.id == module_id:
                return module
        raise PlanError(f"unknown module id: '{module_id}'")

    def topological_order(self) -> list[str]:
        """Return module ids in dependency order; raises on cycles."""
        remaining = {m.id: set(m.depends_on) for m in self.modules}
        order: list[str] = []
        while remaining:
            ready = sorted(i for i, deps in remaining.items() if not deps)
            if not ready:
                raise PlanError(f"dependency cycle among modules: {sorted(remaining)}")
            for module_id in ready:
                order.append(module_id)
                del remaining[module_id]
            for deps in remaining.values():
                deps.difference_update(ready)
        return order


def _load_yaml(path: str | Path, error: type[ValueError]) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise error(f"plan file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise error(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise error(f"plan root must be a mapping, got: {type(raw).__name__}")
    return raw


def _validate_steps(module_id: str, steps: list[Step]) -> None:
    if not steps:
        raise PlanError(f"module '{module_id}': acceptance must define at least one step")
    for step in steps:
        for check in step.checks:
            if check.type not in CHECK_REGISTRY:
                raise PlanError(
                    f"module '{module_id}' step '{step.id}': unknown check type "
                    f"'{check.type}'. available: {sorted(CHECK_REGISTRY)}"
                )


#: Marker written into scaffolded plans by ``harness branch``. A scaffold
#: is structurally valid but says nothing, so validating one must fail — or the
#: Planner is told its placeholder is a plan.
SCAFFOLD_MARKER = "TODO(Planner)"


def load_plan(path: str | Path) -> Plan:
    """Load and validate a plan from a YAML file."""
    text = Path(path).read_text(encoding="utf-8") if Path(path).is_file() else ""
    if SCAFFOLD_MARKER in text:
        raise PlanError(
            f"{path} is still the scaffold, not a plan: it contains "
            f"'{SCAFFOLD_MARKER}'. Replace every TODO — the goal, the report "
            "metrics, and each module's brief, deliverables, and acceptance — "
            "then validate again."
        )
    raw = _load_yaml(path, PlanError)
    if "plan" not in raw or not isinstance(raw["plan"], dict):
        raise PlanError("plan file must have a top-level 'plan:' mapping")

    data = raw["plan"]
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise PlanError("'plan.name' must be a non-empty string")
    goal = data.get("goal", "")
    if not isinstance(goal, str) or not goal.strip():
        raise PlanError("'plan.goal' must be a non-empty string")

    integration = data.get("integration", {}).get("spec")
    if integration is not None and not isinstance(integration, str):
        raise PlanError("'plan.integration.spec' must be a path string")

    modules_data = data.get("modules", [])
    if not isinstance(modules_data, list) or not modules_data:
        raise PlanError("'plan.modules' must be a non-empty list")

    modules: list[Module] = []
    for entry in modules_data:
        module = _module_from_dict(entry)
        _validate_steps(module.id, module.acceptance)
        modules.append(module)

    _validate_dag(modules)
    _validate_disjoint_deliverables(modules)

    report = Report.from_dict(data.get("report"))

    if integration is not None:
        source_dir = Path(path).resolve().parent
        integration_path = Path(integration)
        candidates = [
            integration_path,
            source_dir / integration_path,
            source_dir.parent / integration_path,
            source_dir.parent / ".harness" / integration_path,
            source_dir.parent / ".harness" / "configs" / integration_path.name,
        ]
        if not any(c.is_file() for c in candidates):
            raise PlanError(f"integration spec not found: {integration}")

    return Plan(
        name=name,
        goal=goal,
        description=str(data.get("description", "")),
        integration=integration,
        report=report,
        modules=modules,
        source=Path(path),
    )


def _module_from_dict(entry: Any) -> Module:
    if not isinstance(entry, dict):
        raise PlanError(f"module must be a mapping, got: {entry!r}")
    module_id = entry.get("id")
    if not isinstance(module_id, str) or not module_id:
        raise PlanError(f"module requires a non-empty 'id', got: {entry!r}")
    brief = entry.get("brief")
    if not isinstance(brief, str) or not brief.strip():
        raise PlanError(f"module '{module_id}': 'brief' must be a non-empty string")

    depends_on = entry.get("depends_on", [])
    if not isinstance(depends_on, list):
        raise PlanError(f"module '{module_id}': 'depends_on' must be a list")

    acceptance_data = entry.get("acceptance", {})
    if not isinstance(acceptance_data, dict) or "steps" not in acceptance_data:
        raise PlanError(f"module '{module_id}': 'acceptance' must be a mapping with a 'steps' list")
    try:
        acceptance = [Step.from_dict(s) for s in acceptance_data["steps"]]
    except SpecError as exc:
        raise PlanError(f"module '{module_id}': invalid acceptance: {exc}") from exc

    executor = normalize_executor(str(entry.get("executor", "sub")))
    if executor is None:
        raise PlanError(
            f"module '{module_id}': unknown executor '{entry.get('executor')}'. "
            "available: main (the Planner does it), sub (delegate to a Sub-Worker)"
        )

    return Module(
        id=module_id,
        title=str(entry.get("title", module_id)),
        brief=brief,
        executor=executor,
        depends_on=[str(d) for d in depends_on],
        deliverables=[str(d) for d in entry.get("deliverables", [])],
        constraints=[str(c) for c in entry.get("constraints", [])],
        contract=Contract.from_dict(entry.get("contract")),
        acceptance=acceptance,
    )


def _validate_disjoint_deliverables(modules: list[Module]) -> None:
    """Two modules claiming the same file is a planning error, not a merge problem.

    Workers own their deliverables outright; overlapping ownership means two
    Workers would write the same file with no contract between them.
    """
    owners: dict[str, list[str]] = {}
    for module in modules:
        for deliverable in module.deliverables:
            owners.setdefault(deliverable, []).append(module.id)
    shared = {path: ids for path, ids in owners.items() if len(ids) > 1}
    if shared:
        detail = "; ".join(f"'{path}' claimed by {ids}" for path, ids in sorted(shared.items()))
        raise PlanError(
            f"deliverables must be owned by exactly one module: {detail}. "
            "Split the file, or give one module ownership and a contract to the other."
        )


def _validate_dag(modules: list[Module]) -> None:
    ids = [m.id for m in modules]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise PlanError(f"duplicate module ids: {duplicates}")
    known = set(ids)
    for module in modules:
        unknown = [d for d in module.depends_on if d not in known]
        if unknown:
            raise PlanError(f"module '{module.id}' depends on unknown module(s): {unknown}")
        if module.depends_on and module.id in module.depends_on:
            raise PlanError(f"module '{module.id}' depends on itself")
    Plan(name="_dag", goal="_", modules=modules).topological_order()


# ---------------------------------------------------------------------------
# Approval — a plan is a proposal until someone says otherwise


def plan_fingerprint(path: str | Path) -> str:
    """sha256 of the plan file, so an approval cannot outlive the plan it read."""
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def approval_path(plan_path: str | Path) -> Path:
    """Where a plan's approval record lives — beside the plan, not in results.

    Approval is part of the branch's record, so it belongs with the plan on
    the branch rather than in a gitignored results directory.
    """
    plan_path = Path(plan_path)
    return plan_path.with_suffix(plan_path.suffix + ".approved")


def estimate_cost(plan: Plan, attempts: int, timeout: float) -> dict[str, Any]:
    """A worst case, stated up front.

    A plan looks cheap when the expensive part is invisible. Two Worker modules
    at six attempts and a thirty-minute cap is a six-hour ceiling, and nobody
    approving the plan can see that unless it is written down.
    """
    worker_modules = [m for m in plan.modules if m.executor == "sub"]
    planner_modules = [m for m in plan.modules if m.executor == "main"]
    return {
        "worker_modules": len(worker_modules),
        "planner_modules": len(planner_modules),
        "attempts_per_module": attempts,
        "timeout_s": timeout,
        "worst_case_s": len(worker_modules) * attempts * timeout,
    }


def record_approval(plan_path: str | Path, by: str, note: str = "") -> Path:
    """Record that a human agreed to this exact plan."""
    from datetime import datetime, timezone

    plan_path = Path(plan_path)
    target = approval_path(plan_path)
    target.write_text(
        yaml.safe_dump(
            {
                "plan": str(plan_path.name),
                "fingerprint": plan_fingerprint(plan_path),
                "approved_by": by,
                "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "note": note,
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return target


def approved_by(plan_path: str | Path) -> str:
    """Who recorded the approval, or "" when there is none to read."""
    record = approval_path(Path(plan_path))
    if not record.is_file():
        return ""
    try:
        data = yaml.safe_load(record.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ""
    return str(data.get("approved_by", "") or "")


def approval_status(plan_path: str | Path) -> tuple[bool, str]:
    """Return ``(approved, reason)`` for the plan file as it stands right now."""
    plan_path = Path(plan_path)
    record = approval_path(plan_path)
    if not record.is_file():
        return False, "this plan has never been approved"
    try:
        data = yaml.safe_load(record.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return False, f"approval record is unreadable: {exc}"
    recorded = str(data.get("fingerprint", ""))
    current = plan_fingerprint(plan_path)
    if recorded != current:
        return False, (
            f"the plan changed after it was approved by "
            f"{data.get('approved_by', 'someone')} at {data.get('approved_at', 'unknown time')}"
        )
    return True, f"approved by {data.get('approved_by')} at {data.get('approved_at')}"
