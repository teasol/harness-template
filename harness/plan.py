"""Orchestration plans — the Planner agent's output.

A plan decomposes a goal into modules with explicit contracts (typed
inputs/outputs), dependencies forming a DAG, a worker brief per module, and
machine-checkable acceptance criteria. Plans are materialized into
self-contained task files (see :mod:`harness.task`) that Worker agents
execute one at a time, each in isolation.

A plan also declares what the experiment *reports* back to the researcher
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
    """What this experiment reports back to the researcher.

    Free-form by design: the researcher says what they want when they give
    the instruction. Self-contained by rule: a report may only draw on this
    experiment's own artifacts, never another experiment's, so it can be
    judged on its own terms.
    """

    question: str = ""
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
            question=str(data.get("question", "")),
            metrics=[MetricRef.from_dict(m) for m in data.get("metrics", [])],
            artifacts=artifacts,
        )


def _require_self_contained(path: str, where: str) -> None:
    """Reject a report path that could reach outside this experiment.

    Cross-experiment comparison is the researcher's job (Tier 1), performed by
    collecting finished reports. An experiment that reads another experiment's
    files cannot be judged on its own, so the harness refuses to produce one.
    """
    if path.startswith(("/", "~")):
        raise PlanError(
            f"{where} must stay inside the experiment: absolute path '{path}'. "
            "Reports may only draw on this experiment's own artifacts."
        )
    if ".." in Path(path).parts:
        raise PlanError(
            f"{where} must stay inside the experiment: '{path}' escapes via '..'. "
            "Comparing experiments is the researcher's job, not the plan's."
        )


@dataclasses.dataclass
class Module:
    """One unit of work in a plan: owned by exactly one Worker task."""

    id: str
    title: str
    brief: str
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


def load_plan(path: str | Path) -> Plan:
    """Load and validate a plan from a YAML file."""
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

    return Module(
        id=module_id,
        title=str(entry.get("title", module_id)),
        brief=brief,
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
