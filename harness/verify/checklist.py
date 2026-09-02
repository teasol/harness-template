"""The checklist: named tests, written by the project, addressed as `module:name`.

"Does the code work" is not the same question twice. A log parser needs a
fixture it must parse and one it must reject; a training loop needs the number
in the config to reach the optimizer and the loss to move. No set of built-in
assertions covers both, and the ones that tried covered neither well: the
harness used to ship four check types (`file_exists`, `file_hash`,
`json_metric`, `text_contains`) and a spec language to arrange them, which meant
every project expressed its tests in a vocabulary designed for no project in
particular.

So the harness ships no assertions at all now. It manages a checklist. Each item
names a command the project already has — pytest, a shell script, a CLI of its
own — and the verdict is that command's **exit code**, which is the one thing
that means the same in every project. Writing the test is the project's job,
because only the project knows what would count as working. Knowing which tests
exist, which module each belongs to, and which of them pass right now is the
harness's job.

An item is addressed as ``<module>:<name>``, so `harness check loader:parses-v2`
and a line in a report and an entry in a task file all say the same thing.
"""

from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from harness.verify import heartbeat

#: Item names are addressable, so they live under the same rule as plan names:
#: lowercase, digits, hyphens. A name with a colon in it could not be addressed
#: at all, since the colon is what separates it from its module.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: Reserved for the plan-level checklist — the items that judge the assembled
#: whole rather than one module. Not a module id, so it cannot collide with one.
PLAN_SCOPE = ""

_RUN_ENV_KEYS = (
    "HARNESS_RESULTS_DIR",
    "HARNESS_RUN_ID",
    "HARNESS_PYTHON",
    "HARNESS_DIR",
    "PROJECT_PYTHON",
)


class ChecklistError(ValueError):
    """Raised when a checklist cannot be read, or an item cannot be addressed."""


@dataclasses.dataclass
class Item:
    """One named test, and the command that decides it."""

    name: str
    run: str
    #: Which module it belongs to. Empty means the plan-level checklist.
    module: str = PLAN_SCOPE
    timeout: int | float | None = None
    cwd: str | None = None
    env: dict[str, str] = dataclasses.field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.module}:{self.name}" if self.module else self.name

    @classmethod
    def from_dict(cls, data: object, module: str = PLAN_SCOPE) -> Item:
        if not isinstance(data, dict):
            raise ChecklistError(f"checklist item must be a mapping, got: {data!r}")
        name = data.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ChecklistError(
                f"checklist item name {name!r} must be lowercase letters, digits and hyphens"
            )
        run = data.get("run")
        if not isinstance(run, str) or not run.strip():
            raise ChecklistError(
                f"checklist item '{name}' has no `run` command. An item the harness "
                "cannot run is a note, not a test"
            )
        timeout = data.get("timeout")
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
            raise ChecklistError(f"checklist item '{name}': timeout must be a positive number")
        env = data.get("env", {}) or {}
        if not isinstance(env, dict):
            raise ChecklistError(f"checklist item '{name}': env must be a mapping")
        return cls(
            name=name,
            run=run.strip(),
            module=module,
            timeout=timeout,
            cwd=data.get("cwd"),
            env={str(k): str(v) for k, v in env.items()},
        )

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"name": self.name, "run": self.run}
        if self.timeout is not None:
            data["timeout"] = self.timeout
        if self.cwd:
            data["cwd"] = self.cwd
        if self.env:
            data["env"] = dict(self.env)
        return data


def parse_list(data: object, module: str = PLAN_SCOPE) -> list[Item]:
    """Read a `checklist:` list, rejecting duplicate names within it."""
    if data is None:
        return []
    if not isinstance(data, list):
        raise ChecklistError("`checklist` must be a list of items")
    items = [Item.from_dict(entry, module) for entry in data]
    seen: set[str] = set()
    for item in items:
        if item.name in seen:
            raise ChecklistError(
                f"checklist item '{item.ref}' is declared twice. Names are how an "
                "item is addressed, so two of them cannot share one"
            )
        seen.add(item.name)
    return items


def parse_ref(ref: str) -> tuple[str, str]:
    """``"loader:parses-v2"`` -> ``("loader", "parses-v2")``; ``"loader"`` -> ``("loader", "")``.

    A bare module means every item in it, which is the common case: you run a
    module's checklist far more often than one item of it.
    """
    module, _, name = ref.partition(":")
    return module, name


@dataclasses.dataclass
class ItemResult:
    """What happened when one item ran."""

    module: str
    name: str
    command: str
    exit_code: int | None
    duration_s: float
    log_path: str
    timed_out: bool = False

    @property
    def ref(self) -> str:
        return f"{self.module}:{self.name}" if self.module else self.name

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def detail(self) -> str:
        if self.timed_out:
            return f"timed out after {self.duration_s:.1f}s"
        return f"exit {self.exit_code}"


@dataclasses.dataclass
class ChecklistRun:
    """One pass over a set of items."""

    scope: str
    started_at: str
    finished_at: str
    run_dir: str
    results: list[ItemResult] = dataclasses.field(default_factory=list)
    provenance: dict[str, object] = dataclasses.field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Empty is not passing. Nothing ran, so nothing was established."""
        return bool(self.results) and all(r.passed for r in self.results)

    @property
    def failures(self) -> list[ItemResult]:
        return [r for r in self.results if not r.passed]


def _harness_dir(root: Path) -> Path:
    """`<root>/.harness` when it exists, else the root itself."""
    from harness.paths import get_harness_dir

    candidate = get_harness_dir(root)
    return candidate if candidate.is_dir() else root


def _project_python(root: Path) -> str:
    """The project's own interpreter, if it declared one. Never fatal."""
    from harness.project import ProjectError, load_project_context

    try:
        return load_project_context(root).python
    except ProjectError:
        return ""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _python_path(root: Path, existing: str | None) -> str:
    """Put the tree being tested ahead of anything installed.

    Without this an item run inside a plan's worktree would import the main
    checkout's code (an editable install points at one tree only), so the plan
    would be testing somebody else's source.
    """
    entries = [str(root)] + [str(root / "src")] * (root / "src").is_dir()
    if existing:
        entries.append(existing)
    return os.pathsep.join(entries)


def _format_log(item: Item, result: ItemResult, stdout: str, stderr: str) -> str:
    head = [
        f"# {item.ref}",
        f"# command : {item.run}",
        f"# exit    : {'timeout' if result.timed_out else result.exit_code}",
        f"# duration: {result.duration_s:.2f}s",
        "",
        "## stdout",
        stdout.rstrip() or "(empty)",
        "",
        "## stderr",
        stderr.rstrip() or "(empty)",
        "",
    ]
    return "\n".join(head)


def run_items(
    items: list[Item],
    root: str | Path = ".",
    results_dir: str | Path = "results",
    scope: str = "",
    stop_on_failure: bool = False,
    progress=None,
) -> ChecklistRun:
    """Run every item and record what each one's command returned.

    Items do not stop at the first failure by default: a checklist is read as a
    whole, and knowing that three of five fail is worth more than knowing the
    first one does.

    The environment each command receives is fixed, so a project's test can find
    its way around without hardcoding paths: ``HARNESS_RESULTS_DIR`` (this run's
    directory), ``HARNESS_RUN_ID``, ``HARNESS_PYTHON`` (the harness's own
    interpreter), ``HARNESS_DIR``, and ``PROJECT_PYTHON`` when the project
    declared one — the harness's interpreter usually has none of the project's
    dependencies, so a test that needs torch must not reach for it.
    """
    from harness.verify.provenance import collect_provenance

    root = Path(root).resolve()
    results_root = Path(results_dir)
    if not results_root.is_absolute():
        results_root = root / results_root

    started = _utcnow()
    label = scope or "checklist"
    run_dir = results_root / "checks" / f"{label}-{started.strftime('%Y%m%dT%H%M%S-%f')}"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    saved = {key: os.environ.get(key) for key in _RUN_ENV_KEYS}
    os.environ["HARNESS_RESULTS_DIR"] = str(run_dir)
    os.environ["HARNESS_RUN_ID"] = label
    os.environ["HARNESS_PYTHON"] = sys.executable or "python3"
    os.environ["HARNESS_DIR"] = str(_harness_dir(root))
    project_python = _project_python(root)
    if project_python:
        os.environ["PROJECT_PYTHON"] = project_python
    else:
        os.environ.pop("PROJECT_PYTHON", None)

    results: list[ItemResult] = []
    try:
        base_env = os.environ.copy()
        base_env["PYTHONPATH"] = _python_path(root, base_env.get("PYTHONPATH"))
        for index, item in enumerate(items):
            result = _run_one(item, index, len(items), base_env, root, logs_dir, results_root)
            results.append(result)
            if progress is not None:
                mark = "PASS" if result.passed else "FAIL"
                progress(f"  {mark}  {result.ref}  ({result.detail})")
            if stop_on_failure and not result.passed:
                break
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    return ChecklistRun(
        scope=scope,
        started_at=started.isoformat(),
        finished_at=_utcnow().isoformat(),
        run_dir=str(run_dir),
        results=results,
        provenance=collect_provenance(root),
    )


def _run_one(
    item: Item,
    index: int,
    total: int,
    base_env: dict[str, str],
    root: Path,
    logs_dir: Path,
    results_root: Path,
) -> ItemResult:
    log_path = logs_dir / f"{index:02d}-{item.name}.log"
    env = dict(base_env)
    env.update(item.env)
    cwd = root / item.cwd if item.cwd else root

    start = time.monotonic()
    exit_code: int | None = None
    stdout = stderr = ""
    timed_out = False
    # A command's output is buffered until it exits, so without this a slow test
    # is indistinguishable from a hung one for its whole duration.
    beat = heartbeat.Beat(
        results_root,
        activity="check",
        label=item.ref,
        position=f"{index + 1}/{total}",
        timeout_s=item.timeout,
        detail={"log": str(log_path)},
    )
    try:
        with beat:
            proc = subprocess.run(  # noqa: S602 - running the project's own command is the point
                item.run,
                shell=True,
                executable="/bin/bash",
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=item.timeout,
                check=False,
            )
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")

    result = ItemResult(
        module=item.module,
        name=item.name,
        command=item.run,
        exit_code=exit_code,
        duration_s=time.monotonic() - start,
        log_path=str(log_path),
        timed_out=timed_out,
    )
    log_path.write_text(_format_log(item, result, stdout, stderr), encoding="utf-8")
    return result
