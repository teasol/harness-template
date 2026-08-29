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

import contextlib
import dataclasses
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from harness import guard, heartbeat
from harness.paths import get_agents_config_path
from harness.report import write_reports
from harness.runner import RunResult
from harness.task import Task, _now, block, load_task, save_task, verify_task

DEFAULT_ATTEMPTS = 6
DEFAULT_TIMEOUT = 1800
DEFAULT_CONFIG_PATH = "configs/agents.yaml"
LEGACY_CONFIG_PATH = "configs/worker.yaml"
DEFAULT_PLANNER_ATTEMPTS = 3

#: Below this, an agent invocation cannot have done real work — it is a broken
#: command line, and retrying it just delays the diagnosis.
MIN_PLAUSIBLE_ATTEMPT_S = 5.0

#: How many consecutive attempts may leave every deliverable untouched before
#: the harness concludes the Worker is wedged and hands back to the Planner.
#: One is normal (an agent may read before it writes); a run of them is not.
MAX_CONSECUTIVE_NOOP_ATTEMPTS = 3

#: How much of a failing step's output to put in front of the caller. Enough to
#: name the cause; the full log stays in the run directory.
_FAILURE_TAIL_CHARS = 600


def _as_text(value: object) -> str:
    """Decode captured output that may be bytes, str, or absent."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _failure_summary(result: RunResult, verbose: bool = False) -> str:
    """Say *why* acceptance failed, not merely that it did.

    'acceptance failed' sends the reader hunting through run directories for
    the one line that matters. The cause is usually the tail of the first
    failing step's log, so put it where the failure is reported.
    """
    for step in result.steps:
        if step.success:
            continue
        failed_checks = [c.detail for c in step.checks if not c.passed]
        head = f"acceptance failed at step '{step.step_id}' (exit={step.exit_code})"
        detail = ""
        log_path = Path(step.log_path) if step.log_path else None
        if log_path and log_path.is_file():
            text = log_path.read_text(encoding="utf-8", errors="replace").strip()
            tail = text[-_FAILURE_TAIL_CHARS:]
            if verbose and tail:
                detail = f"\n{tail}"
            elif tail:
                last = [ln for ln in tail.splitlines() if ln.strip()]
                if last:
                    detail = f": {last[-1][:200]}"
        if failed_checks and not detail:
            detail = f": {failed_checks[0][:200]}"
        return head + detail
    return "acceptance failed"


def _deliverable_hashes(task: Task, root: Path) -> dict[str, str]:
    """Hash every declared deliverable; missing files hash as absent."""
    hashes: dict[str, str] = {}
    for rel in task.deliverables:
        path = root / rel
        hashes[rel] = guard._hash_file(path) if path.is_file() else ""
    return hashes


def _log_attempt(
    task: Task, config: AgentConfig, label: str, number: int, attempt: Attempt
) -> None:
    tier = " ".join(x for x in (config.platform, config.model, config.effort) if x)
    task.log.append(
        f"{_now()} worker {label}"
        + (f" [{tier}]" if tier else "")
        + f" attempt {number}/{config.attempts}: {attempt.detail}"
    )
    save_task(task)


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
    #: Containment level. ``strict`` (default) also fails a task whose Worker
    #: modified tracked files it never declared; ``warn`` keeps only the
    #: harness-self-modification guard; ``off`` disables both — for labs that
    #: deliberately develop the harness and the project in one tree.
    guard: str = "strict"

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
            guard=str(data.get("guard", "strict")),
        )
        if config.guard not in ("strict", "warn", "off"):
            raise WorkerError(f"unknown guard level '{config.guard}'. available: strict, warn, off")
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
    #: Paths the Worker changed that it had no contract to change.
    guard_violations: list[str] = dataclasses.field(default_factory=list)

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
    command = render_command(config, root, brief_path, first_attempt, **extra)
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


def render_command(
    config: AgentConfig,
    root: Path,
    brief_path: Path,
    first_attempt: bool = True,
    **extra: str,
) -> str:
    """Return the command line that will actually run, placeholders resolved.

    Callers log *this*, never the raw template. Logging the template hides the
    two things a reader needs when an invocation misbehaves — which model was
    selected, and whether this attempt used ``command`` or ``resume_command``.
    """
    template = config.command
    if not first_attempt and config.resume_command:
        template = config.resume_command
    return template.format(
        brief_file=str(brief_path),
        root=str(root),
        model=config.model,
        effort=config.effort,
        session=config.session,
        **extra,
    )


def run_task(
    tasks_dir: str | Path,
    task_id: str,
    config: AgentConfig | None = None,
    root: str | Path = ".",
    results_dir: str | Path = "results",
    worker_name: str | None = None,
    position: tuple[int, int] | None = None,
    progress: Callable[[str], None] | None = None,
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
    results_root = brief_dir
    brief_dir = brief_dir / "workers" / task.id
    brief_dir.mkdir(parents=True, exist_ok=True)
    brief_path = brief_dir / "brief.md"
    prompt = build_brief(task, root)
    brief_path.write_text(prompt, encoding="utf-8")
    outcome.brief_path = str(brief_path)

    # A module the Planner declared as its own is never handed to a Worker.
    # Briefing an agent to run an experiment or read a log costs more than
    # doing it — the isolation a Worker buys is only worth its price when new
    # code is being written.
    if task.executor == "planner":
        outcome.status = "needs_planner"
        outcome.message = (
            f"module '{task.id}' declares `executor: planner` — the Planner does this one "
            f"itself, no Worker is spawned. Do the work, then:\n"
            f"  python -m harness task verify --id {task.id}\n"
            f"  python -m harness task done --id {task.id} --by planner\n"
            f"Brief: {brief_path}"
        )
        return outcome

    if config.adapter == "manual":
        outcome.status = "needs_human"
        outcome.message = (
            f"briefing written to {brief_path}. Hand it to a Worker session, then run "
            f"`python -m harness task done --id {task.id}`."
        )
        return outcome

    consecutive_noop = 0
    for number in range(1, config.attempts + 1):
        attempt = Attempt(number=number)
        started = time.monotonic()
        rendered = render_command(
            config,
            root,
            brief_path,
            first_attempt=number == 1,
            task_id=task.id,
            task_file=str(task.path),
        )
        # Snapshot both boundaries before handing control to the agent.
        harness_before = guard.snapshot_harness() if config.guard != "off" else {}
        repo_modified_before, repo_created_before = (
            guard.repo_changes(root) if config.guard != "off" else (set(), set())
        )
        deliverables_before = _deliverable_hashes(task, root)

        def _write_attempt_log(
            exit_code: object,
            stdout: str,
            stderr: str,
            _n: int = number,
            _cmd: str = rendered,
        ) -> None:
            (brief_dir / f"attempt-{_n:02d}.log").write_text(
                f"$ {_cmd}\nexit={exit_code}\n\n## stdout\n{stdout}\n\n## stderr\n{stderr}\n",
                encoding="utf-8",
            )

        where = f"{position[0]}/{position[1]}" if position else ""
        if progress:
            cap = f", cap {int(config.timeout)}s" if config.timeout else ""
            progress(
                f"    attempt {number}/{config.attempts} started{cap} "
                f"— `harness progress` shows it while it runs"
            )

        # An agent attempt buffers everything until it exits. Without a
        # heartbeat, a 30-minute cap is 30 minutes in which a working agent and
        # a wedged one look exactly the same.
        beat = heartbeat.Beat(
            results_root,
            activity="worker",
            label=task.id,
            position=f"module {where} · attempt {number}/{config.attempts}"
            if where
            else f"attempt {number}/{config.attempts}",
            timeout_s=config.timeout,
            detail={"plan": task.plan, "model": config.model, "log": str(brief_dir)},
        )
        try:
            with beat:
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
            _write_attempt_log(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired as exc:
            attempt.detail = f"worker timed out after {config.timeout}s"
            # A timeout used to leave no record at all, so the most expensive
            # attempt in a run was the only one you could not inspect.
            # TimeoutExpired carries whatever the process managed to emit.
            _write_attempt_log(
                "timeout",
                _as_text(exc.stdout),
                _as_text(exc.stderr) + f"\n[timed out after {config.timeout}s]",
            )
        except OSError as exc:
            attempt.duration_s = time.monotonic() - started
            attempt.detail = f"could not invoke worker: {exc}"
            outcome.attempts.append(attempt)
            outcome.status = "error"
            outcome.message = attempt.detail
            return outcome
        attempt.duration_s = time.monotonic() - started

        # --- containment ------------------------------------------------
        # Checked before acceptance: a Worker that edited the harness may have
        # made acceptance pass by changing the judge, so a pass here is not
        # evidence of anything.
        if config.guard != "off":
            touched = guard.changed_paths(harness_before, guard.snapshot_harness())
            if touched:
                attempt.detail = (
                    "worker modified the harness itself — "
                    + ", ".join(Path(p).name for p in touched[:5])
                    + (f" (+{len(touched) - 5} more)" if len(touched) > 5 else "")
                )
                outcome.attempts.append(attempt)
                _log_attempt(task, config, label, number, attempt)
                outcome.status = "error"
                outcome.guard_violations = touched
                outcome.message = (
                    f"task '{task.id}' aborted: the Worker changed the harness package "
                    f"({len(touched)} file(s)). Acceptance run under a modified harness "
                    "proves nothing, so this is not retried. Review and revert:\n  "
                    + "\n  ".join(touched)
                )
                block(tasks_dir, task_id, "worker modified the harness package")
                return outcome

        result = verify_task(task, root=root, results_dir=results_dir)
        write_reports(result)
        attempt.passed = result.success
        if not attempt.detail:
            attempt.detail = "acceptance passed" if result.success else _failure_summary(result)

        # Judge progress before logging, so the audit trail records not just
        # that the attempt failed but whether it moved anything.
        deliverables_after = _deliverable_hashes(task, root)
        if result.success:
            consecutive_noop = 0
        elif deliverables_before == deliverables_after:
            consecutive_noop += 1
            attempt.detail += " (no deliverable changed)"
        else:
            consecutive_noop = 0

        outcome.attempts.append(attempt)
        _log_attempt(task, config, label, number, attempt)
        if progress:
            progress(
                f"    attempt {number}/{config.attempts} "
                f"({heartbeat.human_duration(attempt.duration_s)}): {attempt.detail}"
            )

        if result.success:
            task.status = "done"
            task.worker = label
            task.log.append(f"{_now()} acceptance passed on attempt {number} — done")
            save_task(task)
            outcome.status = "done"
            outcome.message = f"task '{task.id}' done after {number} attempt(s)"
            return outcome

        # --- scope: did it change anything it never declared? -------------
        if config.guard == "strict":
            modified, created = guard.undeclared_changes(
                root, task.deliverables, repo_modified_before, repo_created_before
            )
            if created:
                task.log.append(
                    f"{_now()} note: undeclared new file(s) after attempt {number}: "
                    + ", ".join(created[:5])
                )
                save_task(task)
            if modified:
                outcome.status = "error"
                outcome.guard_violations = modified
                outcome.message = (
                    f"task '{task.id}' aborted: the Worker modified tracked file(s) it "
                    f"never declared as deliverables. An undeclared change is one nothing "
                    f"checks. Review and revert:\n  " + "\n  ".join(modified)
                )
                block(tasks_dir, task_id, "worker modified undeclared tracked files")
                return outcome

        # --- is retrying going to achieve anything? ----------------------
        # A run of attempts that leaves every deliverable byte-identical is a
        # wedged resume-session: the agent believes it is already finished, so
        # it edits nothing and the next attempt repeats exactly. One such
        # attempt is normal — an agent may spend it reading before it writes —
        # so only a run of them is evidence. And if this was the last attempt
        # anyway, the ordinary cap path already says the right thing.
        if consecutive_noop >= MAX_CONSECUTIVE_NOOP_ATTEMPTS and number < config.attempts:
            outcome.status = "failed"
            outcome.message = (
                f"task '{task.id}' stopped after attempt {number}: {consecutive_noop} "
                "consecutive attempts changed no deliverable, so the remaining "
                f"{config.attempts - number} would repeat the same failure. The brief or "
                "the acceptance is wrong — that is the Planner's call.\n"
                f"{_failure_summary(result, verbose=True)}"
            )
            block(tasks_dir, task_id, f"worker made no progress for {consecutive_noop} attempts")
            return outcome

        # An agent cannot have done real work in a couple of seconds; that is
        # what a misconfigured command line looks like, and retrying a broken
        # invocation five more times only delays the diagnosis.
        if attempt.duration_s < MIN_PLAUSIBLE_ATTEMPT_S and attempt.exit_code not in (0, None):
            outcome.status = "error"
            outcome.message = (
                f"task '{task.id}' aborted: the Worker command exited in "
                f"{attempt.duration_s:.2f}s with code {attempt.exit_code} — too fast to have "
                "done any work. This is a worker configuration problem, not a coding "
                f"problem. Check `harness setup --check`.\n  $ {rendered}"
            )
            block(tasks_dir, task_id, "worker command failed immediately — misconfigured")
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


def reconcile_worker_record(
    task_id: str,
    task_status: str,
    results_dir: str | Path = "results",
    root: str | Path = ".",
    note: str = "",
) -> Path | None:
    """Bring a Worker's record back in line with what happened to its task.

    ``worker.json`` records how the Worker loop ended. The task can end
    differently afterwards: a Planner that verifies a blocked task by hand and
    marks it done leaves the record permanently claiming ``failed`` for a task
    the board calls ``done``. Two files disagreeing about the same event make
    the audit trail worth less than no audit trail, because a reader has no way
    to tell which one is lying.

    The Worker's own history is preserved — attempts are not rewritten. Only
    the final status is corrected, and the correction says who made it.
    """
    base = Path(results_dir)
    if not base.is_absolute():
        base = Path(root) / base
    path = base / "workers" / task_id / "worker.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    worker_status = str(data.get("status", ""))
    resolved = "done" if task_status == "done" else worker_status
    if worker_status == resolved and not note:
        return path

    data["status"] = resolved
    data["task_status"] = task_status
    history = data.setdefault("reconciled", [])
    if isinstance(history, list):
        history.append(
            {
                "at": _now(),
                "was": worker_status,
                "now": resolved,
                "note": note or f"task marked '{task_status}' outside the Worker loop",
            }
        )
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path
