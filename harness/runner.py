"""Step runner: executes spec steps and evaluates their checks."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness import checks as checks_mod
from harness import heartbeat
from harness.reproducibility import collect_provenance, math_env, seed_env
from harness.spec import Spec, Step

_RUN_ENV_KEYS = (
    "HARNESS_RESULTS_DIR",
    "HARNESS_RUN_ID",
    "HARNESS_PYTHON",
    "HARNESS_SEED",
    "PROJECT_PYTHON",
)


def _project_python(root: Path) -> str:
    """The project's own interpreter, if it declared one. Never fatal."""
    from harness.project import ProjectError, load_project_context

    try:
        return load_project_context(root).python
    except ProjectError:
        return ""


@dataclasses.dataclass
class CheckResult:
    """Outcome of a single check (or a synthetic runner-level check)."""

    check_type: str
    passed: bool
    detail: str


@dataclasses.dataclass
class StepResult:
    """Outcome of a single step execution."""

    step_id: str
    command: str
    exit_code: int | None
    duration_s: float
    log_path: str
    checks: list[CheckResult] = dataclasses.field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and all(c.passed for c in self.checks)


@dataclasses.dataclass
class RunResult:
    """Outcome of a full spec run."""

    spec_name: str
    started_at: str
    finished_at: str
    success: bool
    run_dir: str
    steps: list[StepResult] = dataclasses.field(default_factory=list)
    provenance: dict[str, Any] = dataclasses.field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Runner:
    """Executes a :class:`~harness.spec.Spec` and produces a :class:`RunResult`.

    Every step runs as a shell command in the repo root (or the step's
    ``cwd``), with output captured to ``<run_dir>/logs/NN-<step_id>.log``.
    The runner exports ``HARNESS_RESULTS_DIR`` and ``HARNESS_RUN_ID`` to each
    step and expands them in check ``path`` params, so steps can write
    artifacts into a per-run directory referenced by checks.
    """

    def __init__(self, root: str | Path = ".", results_dir: str | Path = "results") -> None:
        self.root = Path(root).resolve()
        self.results_dir = Path(results_dir)
        if not self.results_dir.is_absolute():
            self.results_dir = self.root / self.results_dir

    def run(self, spec: Spec, stop_on_failure: bool = True) -> RunResult:
        started = _utcnow()
        run_dir = self.results_dir / "runs" / f"{spec.name}-{started.strftime('%Y%m%dT%H%M%S-%f')}"
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        saved = {key: os.environ.get(key) for key in _RUN_ENV_KEYS}
        os.environ["HARNESS_RESULTS_DIR"] = str(run_dir)
        os.environ["HARNESS_RUN_ID"] = spec.name
        # Steps must not assume a `python` binary exists (Debian/Ubuntu ship only
        # `python3`); they invoke the very interpreter running the harness.
        os.environ["HARNESS_PYTHON"] = sys.executable or "python3"
        # HARNESS_PYTHON is the harness's own interpreter and usually has none
        # of the project's dependencies — a step that needs torch must not
        # reach for it. PROJECT_PYTHON is the project's, when it declares one.
        project_python = _project_python(self.root)
        if project_python:
            os.environ["PROJECT_PYTHON"] = project_python
        else:
            os.environ.pop("PROJECT_PYTHON", None)
        if spec.seed is not None:
            os.environ["HARNESS_SEED"] = str(spec.seed)
        else:
            os.environ.pop("HARNESS_SEED", None)

        # Recorded in provenance so a reader can tell what the harness changed
        # about the environment. A number measured under CUBLAS_WORKSPACE_CONFIG
        # is not comparable to one measured without it, so this must never be
        # invisible.
        injected: dict[str, str] = {}
        if spec.seed is not None:
            injected.update(seed_env(spec.seed))
        if spec.deterministic_math:
            injected.update(math_env())

        step_results: list[StepResult] = []
        try:
            base_env = os.environ.copy()
            base_env["PYTHONPATH"] = _python_path(self.root, base_env.get("PYTHONPATH"))
            base_env.update(injected)
            for index, step in enumerate(spec.steps):
                result = self._run_step(step, index, base_env, logs_dir, len(spec.steps), spec.name)
                step_results.append(result)
                if stop_on_failure and not result.success:
                    break
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        finished = _utcnow()
        all_steps_ran = len(step_results) == len(spec.steps)
        provenance = collect_provenance(self.root, seed=spec.seed)
        provenance["injected_env"] = dict(injected)
        provenance["deterministic_math"] = spec.deterministic_math
        return RunResult(
            spec_name=spec.name,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            success=bool(step_results) and all_steps_ran and all(r.success for r in step_results),
            run_dir=str(run_dir),
            steps=step_results,
            provenance=provenance,
        )

    def _run_step(
        self,
        step: Step,
        index: int,
        base_env: dict[str, str],
        logs_dir: Path,
        total_steps: int = 0,
        spec_name: str = "",
    ) -> StepResult:
        log_path = logs_dir / f"{index:02d}-{step.id}.log"
        step_env = dict(base_env)
        step_env.update(step.env)
        cwd = self.root / step.cwd if step.cwd else self.root

        start = time.monotonic()
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        timed_out = False
        # A step's output is buffered until it exits, so without this a long
        # step is indistinguishable from a hung one for its whole duration.
        beat = heartbeat.Beat(
            self.results_dir,
            activity="step",
            label=step.id,
            position=f"{index + 1}/{total_steps}" if total_steps else "",
            timeout_s=step.timeout,
            detail={"spec": spec_name, "log": str(log_path)},
        )
        try:
            with beat:
                proc = subprocess.run(  # noqa: S602 - shell commands are the point of specs
                    step.run,
                    shell=True,
                    executable="/bin/bash",
                    cwd=str(cwd),
                    env=step_env,
                    capture_output=True,
                    text=True,
                    timeout=step.timeout,
                    check=False,
                )
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _as_text(exc.stdout)
            stderr = _as_text(exc.stderr)
        duration = time.monotonic() - start

        log_path.write_text(
            _format_log(step, exit_code, timed_out, duration, stdout, stderr),
            encoding="utf-8",
        )

        check_results: list[CheckResult] = []
        if timed_out:
            check_results.append(
                CheckResult("timeout", False, f"step timed out after {step.timeout}s")
            )
        elif exit_code != 0:
            check_results.append(
                CheckResult("exit_code", False, f"command exited with code {exit_code}")
            )
        else:
            for check in step.checks:
                try:
                    detail = checks_mod.run_check(check.type, check.params, self.root)
                    check_results.append(CheckResult(check.type, True, detail))
                except checks_mod.CheckError as exc:
                    check_results.append(CheckResult(check.type, False, str(exc)))

        return StepResult(
            step_id=step.id,
            command=step.run,
            exit_code=exit_code,
            duration_s=duration,
            log_path=str(log_path),
            checks=check_results,
        )


def _python_path(root: Path, existing: str | None) -> str:
    """Put the tree being verified ahead of anything installed.

    Without this a step run inside an experiment worktree would import the
    main checkout's code (an editable install points at one tree only), so
    the experiment would verify somebody else's source.
    """
    entries = [str(root)] + [str(root / "src")] * (root / "src").is_dir()
    if existing:
        entries.append(existing)
    return os.pathsep.join(entries)


def _as_text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode(errors="replace")
    return data


def _format_log(
    step: Step,
    exit_code: int | None,
    timed_out: bool,
    duration: float,
    stdout: str,
    stderr: str,
) -> str:
    status = "TIMEOUT" if timed_out else f"exit={exit_code}"
    lines = [
        f"# step:    {step.id}",
        f"# command: {step.run}",
        f"# status:  {status} ({duration:.2f}s)",
        "",
        "## stdout",
        stdout or "(empty)",
        "",
        "## stderr",
        stderr or "(empty)",
        "",
    ]
    return "\n".join(lines)
