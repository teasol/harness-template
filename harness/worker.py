"""Worker adapters — how a Planner actually gets a module built.

The Planner decides *which* task to run; this module runs it. Keeping the
loop here rather than in an agent's head is the point of a harness: retries,
caps, verification, and the audit trail are tested code, not something a
model has to remember to do.

Two adapters ship:

``manual``
    Writes the briefing to a file and stops. A human pastes it into whatever
    agent session they like, then runs ``harness task verify``. This is the
    default so the template works with no configuration and no API key.

``cli``
    Runs a configured shell command — a coding agent in headless mode. The
    harness never names a vendor: the command is configuration, so a lab
    points it at whichever tool (or local model) it already uses.

On failure the harness feeds the actual test output back and asks the same
worker to try again, because a coding agent handed a failing test usually
fixes it, and continuing beats restarting from nothing. Attempts are capped
so a wedged worker cannot burn budget forever.

Cost is *not* estimated. The harness records what it can observe — attempts,
durations, exit codes, the configured adapter — and says so plainly when an
adapter reports no cost of its own.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from harness.report import write_reports
from harness.runner import RunResult
from harness.task import Task, _now, block, load_task, save_task, verify_task

DEFAULT_ATTEMPTS = 6
DEFAULT_TIMEOUT = 1800
DEFAULT_CONFIG_PATH = "configs/agents.yaml"
LEGACY_CONFIG_PATH = "configs/worker.yaml"
DEFAULT_PLANNER_ATTEMPTS = 3


class WorkerError(RuntimeError):
    """Raised when a worker cannot be configured or invoked at all."""


@dataclasses.dataclass
class AgentConfig:
    """How to invoke one tier's agent. Every field is the lab's choice, not ours."""

    adapter: str = "manual"
    platform: str = ""
    model: str = ""
    effort: str = ""
    command: str = ""
    resume_command: str = ""
    attempts: int = DEFAULT_ATTEMPTS
    timeout: int | float = DEFAULT_TIMEOUT
    label: str = "worker"
    #: An existing session to attach to instead of starting a fresh one.
    session: str = ""

    @classmethod
    def from_dict(cls, data: Any, default_attempts: int = DEFAULT_ATTEMPTS) -> AgentConfig:
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise WorkerError(f"'worker' config must be a mapping, got: {data!r}")
        adapter = str(data.get("adapter", "manual"))
        if adapter not in ("manual", "cli"):
            raise WorkerError(f"unknown worker adapter '{adapter}'. available: manual, cli")
        attempts = data.get("attempts", default_attempts)
        if not isinstance(attempts, int) or attempts < 1:
            raise WorkerError(f"'worker.attempts' must be a positive integer, got: {attempts!r}")
        config = cls(
            adapter=adapter,
            platform=str(data.get("platform", "")),
            model=str(data.get("model", "")),
            effort=str(data.get("effort", "")),
            command=str(data.get("command", "")),
            resume_command=str(data.get("resume_command", "")),
            attempts=attempts,
            timeout=data.get("timeout", DEFAULT_TIMEOUT),
            label=str(data.get("label", "worker")),
            session=str(data.get("session", "")),
        )
        if config.adapter == "cli" and not config.command.strip():
            raise WorkerError("worker adapter 'cli' requires a 'command'")
        # A command asking for a model or an effort it was never given would
        # silently run at the platform default — which defeats the point of
        # choosing a tier at all.
        for field in ("model", "effort", "session"):
            if "{" + field + "}" in config.command and not getattr(config, field):
                raise WorkerError(
                    f"the worker command uses {{{field}}} but no '{field}' is set. "
                    f"Run `harness setup` or set '{field}' in the worker config."
                )
        return config


#: Older name, kept so existing configs and callers keep working.
WorkerConfig = AgentConfig


def load_agent_config(
    section: str = "worker",
    path: str | Path | None = None,
    root: str | Path = ".",
) -> AgentConfig:
    """Load one tier's configuration, defaulting to the manual adapter.

    Both tiers live in one file so their models sit side by side: seeing
    `planner: opus` above `worker: haiku` is the tier split made visible.
    """
    root = Path(root)
    default_attempts = DEFAULT_PLANNER_ATTEMPTS if section == "planner" else DEFAULT_ATTEMPTS
    if path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        if not candidate.is_file():
            raise WorkerError(f"{section} config not found: {candidate}")
    else:
        from harness.paths import get_agents_config_path
        candidate = get_agents_config_path(root)
        if not candidate.is_file():
            legacy = root / LEGACY_CONFIG_PATH
            candidate = legacy if legacy.is_file() and section == "worker" else candidate
        if not candidate.is_file():
            return AgentConfig(label=section, attempts=default_attempts)
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise WorkerError(f"invalid YAML in {candidate}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkerError(f"agent config root must be a mapping: {candidate}")
    entry = raw.get(section)
    if entry is None:
        return AgentConfig(label=section, attempts=default_attempts)
    config = AgentConfig.from_dict(entry, default_attempts=default_attempts)
    if config.label == "worker" and section != "worker":
        config.label = section
    return config


def load_worker_config(path: str | Path | None = None, root: str | Path = ".") -> AgentConfig:
    """Backwards-friendly alias for the worker tier."""
    return load_agent_config("worker", path=path, root=root)


# ---------------------------------------------------------------------------
# Briefings


def build_brief(task: Task, root: str | Path = ".") -> str:
    """The complete work order handed to a Worker on its first attempt."""
    lines = [
        f"You are a Worker. Implement exactly one module: task '{task.id}'.",
        "",
        f"Repository root: {Path(root).resolve()}",
        f"Task file:       {task.path}",
        "",
        "Read agents/worker.md for the rules you are bound by. In short:",
        "  - Touch only this task's deliverables. Never modify other modules,",
        "    the plan, other tasks, harness/, or CI.",
        "  - Consume dependencies only through their declared contract.",
        "  - Honor the constraints below; unseeded randomness is a bug.",
        "",
        f"## Title\n{task.title}",
        "",
        f"## Brief\n{task.brief.rstrip()}",
        "",
    ]
    if task.contract.inputs or task.contract.outputs:
        lines.append("## Contract")
        for port in task.contract.inputs:
            lines.append(f"  input  {port.name}: {port.type} — {port.description}".rstrip())
        for port in task.contract.outputs:
            lines.append(f"  output {port.name}: {port.type} — {port.description}".rstrip())
        lines.append("")
    if task.deliverables:
        lines.append("## Deliverables (the harness checks these exist)")
        lines += [f"  - {d}" for d in task.deliverables] + [""]
    if task.constraints:
        lines.append("## Constraints")
        lines += [f"  - {c}" for c in task.constraints] + [""]

    lines += [
        "## Definition of done",
        "These commands are run for you after you finish. They decide the outcome;",
        "your own opinion of the work does not.",
        "",
    ]
    for step in task.acceptance:
        lines.append(f"  $ {step.run}")
    lines += [
        "",
        f"You may run them yourself while working: python -m harness task verify --id {task.id}",
        "",
    ]
    return "\n".join(lines)


def retry_brief(task: Task, result: RunResult, attempt: int, attempts: int) -> str:
    """Hand the worker the real failure output and ask for a fix."""
    failures = [
        f"  [{check.check_type}] {check.detail}"
        for step in result.steps
        for check in step.checks
        if not check.passed
    ]
    logs = []
    for step in result.steps:
        if step.success or not step.log_path:
            continue
        log_file = Path(step.log_path)
        if log_file.is_file():
            logs.append(f"--- {log_file.name} ---\n{log_file.read_text(encoding='utf-8')[-4000:]}")
    return "\n".join(
        [
            f"Your previous attempt at task '{task.id}' did not pass acceptance.",
            f"This is attempt {attempt} of {attempts}. Fix the code; do not start over.",
            "",
            "## Failing checks",
            *(failures or ["  (the step itself failed before any check ran)"]),
            "",
            "## Output",
            *(logs or ["  (no step log captured)"]),
            "",
            "Change the implementation so these pass. Stay within your deliverables.",
        ]
    )


# ---------------------------------------------------------------------------
# Running


@dataclasses.dataclass
class Attempt:
    """One invocation of a Worker plus the verdict the harness reached."""

    number: int
    exit_code: int | None = None
    duration_s: float = 0.0
    passed: bool = False
    detail: str = ""


@dataclasses.dataclass
class WorkerOutcome:
    """The result of running one task to completion, or giving up."""

    task_id: str
    adapter: str
    status: str = "pending"  # done | failed | needs_human | error
    platform: str = ""
    model: str = ""
    effort: str = ""
    attempts: list[Attempt] = dataclasses.field(default_factory=list)
    brief_path: str | None = None
    cost: str = "not measured (adapter reports none)"
    message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "done"


def invoke_agent(
    config: AgentConfig,
    root: Path,
    prompt: str,
    brief_path: Path,
    first_attempt: bool = True,
    **extra: str,
):
    """Run the configured agent command with the briefing on stdin.

    The first attempt uses ``command``; later ones use ``resume_command`` when
    set, so a tool that can continue a session keeps its context rather than
    starting from nothing.
    """
    template = config.command
    if not first_attempt and config.resume_command:
        template = config.resume_command
    command = template.format(
        brief_file=str(brief_path),
        root=str(root),
        model=config.model,
        effort=config.effort,
        session=config.session,
        **extra,
    )
    return subprocess.run(  # noqa: S602 - the command is the lab's configuration
        command,
        shell=True,
        cwd=str(root),
        input=prompt,
        capture_output=True,
        text=True,
        timeout=config.timeout,
        check=False,
    )


def run_task(
    tasks_dir: str | Path,
    task_id: str,
    config: AgentConfig | None = None,
    root: str | Path = ".",
    results_dir: str | Path = "results",
    worker_name: str | None = None,
) -> WorkerOutcome:
    """Invoke a Worker on one task, verifying and retrying until the cap.

    Returns rather than raises for ordinary outcomes: a task that cannot be
    finished is blocked for the Planner, which is a result, not a crash.
    """
    config = config or AgentConfig()
    root = Path(root).resolve()
    task = load_task(tasks_dir, task_id)
    label = worker_name or config.label
    outcome = WorkerOutcome(
        task_id=task.id,
        adapter=config.adapter,
        platform=config.platform,
        model=config.model,
        effort=config.effort,
    )

    brief_dir = Path(results_dir)
    if not brief_dir.is_absolute():
        brief_dir = root / brief_dir
    brief_dir = brief_dir / "workers" / task.id
    brief_dir.mkdir(parents=True, exist_ok=True)
    brief_path = brief_dir / "brief.md"
    prompt = build_brief(task, root)
    brief_path.write_text(prompt, encoding="utf-8")
    outcome.brief_path = str(brief_path)

    if config.adapter == "manual":
        outcome.status = "needs_human"
        outcome.message = (
            f"briefing written to {brief_path}. Hand it to a Worker session, then run "
            f"`python -m harness task done --id {task.id}`."
        )
        return outcome

    for number in range(1, config.attempts + 1):
        attempt = Attempt(number=number)
        started = time.monotonic()
        try:
            proc = invoke_agent(
                config,
                root,
                prompt,
                brief_path,
                first_attempt=number == 1,
                task_id=task.id,
                task_file=str(task.path),
            )
            attempt.exit_code = proc.returncode
            (brief_dir / f"attempt-{number:02d}.log").write_text(
                f"$ {config.resume_command or config.command}\n"
                f"exit={proc.returncode}\n\n## stdout\n{proc.stdout}\n\n## stderr\n{proc.stderr}\n",
                encoding="utf-8",
            )
        except subprocess.TimeoutExpired:
            attempt.detail = f"worker timed out after {config.timeout}s"
        except OSError as exc:
            attempt.duration_s = time.monotonic() - started
            attempt.detail = f"could not invoke worker: {exc}"
            outcome.attempts.append(attempt)
            outcome.status = "error"
            outcome.message = attempt.detail
            return outcome
        attempt.duration_s = time.monotonic() - started

        result = verify_task(task, root=root, results_dir=results_dir)
        write_reports(result)
        attempt.passed = result.success
        if not attempt.detail:
            attempt.detail = "acceptance passed" if result.success else "acceptance failed"
        outcome.attempts.append(attempt)

        tier = " ".join(x for x in (config.platform, config.model, config.effort) if x)
        task.log.append(
            f"{_now()} worker {label}"
            + (f" [{tier}]" if tier else "")
            + f" attempt {number}/{config.attempts}: {attempt.detail}"
        )
        save_task(task)

        if result.success:
            task.status = "done"
            task.worker = label
            task.log.append(f"{_now()} acceptance passed on attempt {number} — done")
            save_task(task)
            outcome.status = "done"
            outcome.message = f"task '{task.id}' done after {number} attempt(s)"
            return outcome

        # Keep the same worker going with the real failure output: a coding
        # agent handed its own failing test usually fixes it, and continuing
        # is cheaper than restarting from a blank slate.
        prompt = retry_brief(task, result, number + 1, config.attempts)
        task = load_task(tasks_dir, task_id)

    outcome.status = "failed"
    outcome.message = (
        f"task '{task_id}' still failing after {config.attempts} attempt(s) "
        "— blocked for the Planner"
    )
    block(tasks_dir, task_id, f"worker {label} exhausted {config.attempts} attempts")
    return outcome


def write_worker_report(
    outcome: WorkerOutcome, results_dir: str | Path, root: str | Path = "."
) -> Path:
    """Record what was observed — never an estimate of what was spent."""
    base = Path(results_dir)
    if not base.is_absolute():
        base = Path(root) / base
    path = base / "workers" / outcome.task_id / "worker.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataclasses.asdict(outcome), indent=2) + "\n", encoding="utf-8")
    return path
