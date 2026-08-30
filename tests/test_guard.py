"""Tests for Worker containment and the retry loop's stopping rules.

Every "worker" here is a shell command, so the whole loop runs with no model,
no network and no API key.

The scenarios are not hypothetical. They are the failure modes observed the
first time this harness was pointed at a real project: an agent patched the
harness to make its own acceptance pass, a resume-session spun through four
attempts editing nothing, and the single most expensive attempt — a 30-minute
timeout — was the one that left no log at all.
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from harness import guard
from harness.task import load_task
from harness.worker import WorkerConfig, WorkerError, run_task

TASK_YAML = """\
task:
  id: widget
  plan: mini
  title: Build the widget
  depends_on: []
  brief: |
    Create src/widget.py.
  contract:
    inputs: []
    outputs: []
  deliverables:
  - src/widget.py
  constraints: []
  acceptance:
    steps:
    - id: check
      run: test -f src/widget.py
      checks:
      - type: file_exists
        path: src/widget.py
  status: todo
  worker: null
  log: []
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "tasks").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tasks" / "widget.task.yaml").write_text(TASK_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def content_project(tmp_path: Path) -> Path:
    """A task whose acceptance needs file *content*, not merely existence."""
    (tmp_path / "tasks").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tasks" / "widget.task.yaml").write_text(
        TASK_YAML.replace(
            "    - id: check\n      run: test -f src/widget.py",
            "    - id: check\n      run: grep -q done src/widget.py",
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def git_project(project: Path) -> Path:
    """The same project under git, so the scope guard has something to read."""
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "init"],
    ):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
    return project


def _config(project: Path, command: str, attempts: int = 6, **kw) -> WorkerConfig:
    return WorkerConfig(adapter="cli", command=command, attempts=attempts, label="fake-agent", **kw)


# ---------------------------------------------------------------------------
# the harness is not the agent's to edit


def test_worker_editing_the_harness_is_fatal_and_not_retried(project: Path) -> None:
    """Acceptance run under a harness the agent just rewrote proves nothing."""
    victim = Path(guard.harness_package_dir()) / "runner.py"
    original = victim.read_text(encoding="utf-8")
    script = project / "sneaky.sh"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            cat > /dev/null
            printf '\\n# touched by the worker\\n' >> "{victim}"
            touch src/widget.py
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)

    try:
        outcome = run_task(
            project / "tasks", "widget", _config(project, "bash sneaky.sh"), root=project
        )
    finally:
        victim.write_text(original, encoding="utf-8")

    assert outcome.status == "error"
    assert outcome.guard_violations
    assert "runner.py" in " ".join(outcome.guard_violations)
    # Fatal means fatal: one attempt, no retries, even though the agent had
    # also produced a deliverable that would have passed acceptance.
    assert len(outcome.attempts) == 1
    assert load_task(project / "tasks", "widget").status == "blocked"


def test_guard_off_allows_harness_edits(project: Path) -> None:
    """Labs that develop the harness alongside the project can opt out."""
    script = project / "ok.sh"
    script.write_text("#!/usr/bin/env bash\ncat > /dev/null\ntouch src/widget.py\n", "utf-8")
    script.chmod(0o755)
    outcome = run_task(
        project / "tasks",
        "widget",
        _config(project, "bash ok.sh", guard="off"),
        root=project,
    )
    assert outcome.succeeded


def test_unknown_guard_level_rejected() -> None:
    with pytest.raises(WorkerError, match="unknown guard level"):
        WorkerConfig.from_dict({"adapter": "cli", "command": "true", "guard": "loose"})


# ---------------------------------------------------------------------------
# undeclared changes


def test_modifying_an_undeclared_tracked_file_is_fatal(git_project: Path) -> None:
    """A change nothing checks is exactly the shape of an unnoticed regression."""
    script = git_project / "greedy.sh"
    script.write_text(
        "#!/usr/bin/env bash\ncat > /dev/null\necho broken >> tasks/../src/other.py\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    (git_project / "src" / "other.py").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=git_project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "add other"], cwd=git_project, check=True, capture_output=True
    )

    outcome = run_task(
        git_project / "tasks", "widget", _config(git_project, "bash greedy.sh"), root=git_project
    )
    assert outcome.status == "error"
    assert any("other.py" in p for p in outcome.guard_violations)


def test_preexisting_dirt_is_not_blamed_on_the_worker(git_project: Path) -> None:
    """State the Worker inherited must not count against it."""
    (git_project / "src" / "other.py").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=git_project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "add other"], cwd=git_project, check=True, capture_output=True
    )
    # Dirty it *before* the worker runs.
    (git_project / "src" / "other.py").write_text("already changed\n", encoding="utf-8")

    script = git_project / "ok.sh"
    script.write_text("#!/usr/bin/env bash\ncat > /dev/null\ntouch src/widget.py\n", "utf-8")
    script.chmod(0o755)
    outcome = run_task(
        git_project / "tasks", "widget", _config(git_project, "bash ok.sh"), root=git_project
    )
    assert outcome.succeeded


# ---------------------------------------------------------------------------
# stopping rules


def test_a_run_of_noop_attempts_stops_early(project: Path) -> None:
    """A wedged resume-session must not spend the whole cap repeating itself."""
    outcome = run_task(
        project / "tasks",
        "widget",
        _config(project, "echo 'I changed nothing'", attempts=6),
        root=project,
    )
    assert outcome.status == "failed"
    assert len(outcome.attempts) == 3, "should stop once three attempts changed nothing"
    assert "changed no deliverable" in outcome.message
    # The failure reason travels with the verdict instead of hiding in a log.
    assert "check" in outcome.message
    log = "\n".join(load_task(project / "tasks", "widget").log)
    assert "no deliverable changed" in log


def test_progress_on_the_deliverable_resets_the_noop_counter(content_project: Path) -> None:
    """Real progress buys more attempts than the no-op cap would allow.

    The counter measures movement on the *deliverable*, deliberately — an agent
    that keeps rewriting scratch files while the contract stays untouched is
    not making progress, and scratch writes must not be able to mask a wedge.
    So this worker edits the deliverable every attempt and needs five of them,
    which is more than MAX_CONSECUTIVE_NOOP_ATTEMPTS.
    """
    script = content_project / "grind.sh"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            cat > /dev/null
            echo step >> src/widget.py
            if [ "$(wc -l < src/widget.py)" -ge 5 ]; then echo done >> src/widget.py; fi
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    outcome = run_task(
        content_project / "tasks",
        "widget",
        _config(content_project, "bash grind.sh", attempts=6),
        root=content_project,
    )
    assert outcome.succeeded
    assert len(outcome.attempts) == 5


def test_instant_failure_is_reported_as_misconfiguration(project: Path) -> None:
    """0.15s and a non-zero exit is a broken command line, not a coding problem."""
    outcome = run_task(
        project / "tasks",
        "widget",
        _config(project, "exit 2", attempts=6),
        root=project,
    )
    assert outcome.status == "error"
    assert len(outcome.attempts) == 1
    assert "worker configuration problem" in outcome.message
    # The message must name the command that actually ran.
    assert "exit 2" in outcome.message


def test_timeout_still_writes_an_attempt_log(project: Path) -> None:
    """The most expensive attempt must not be the one you cannot inspect."""
    script = project / "hang.sh"
    script.write_text(
        "#!/usr/bin/env bash\ncat > /dev/null\necho 'starting work'\nsleep 30\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    outcome = run_task(
        project / "tasks",
        "widget",
        _config(project, "bash hang.sh", attempts=1, timeout=1),
        root=project,
    )
    assert outcome.status == "failed"
    log = project / "results" / "workers" / "widget" / "attempt-01.log"
    assert log.is_file(), "a timed-out attempt must leave a record"
    text = log.read_text(encoding="utf-8")
    assert "timed out after 1s" in text
    assert "bash hang.sh" in text, "the log records the command that actually ran"


def test_attempt_log_records_the_resolved_command(project: Path) -> None:
    """Logging the raw template hides the model and which command was used."""
    script = project / "ok.sh"
    script.write_text("#!/usr/bin/env bash\ncat > /dev/null\ntouch src/widget.py\n", "utf-8")
    script.chmod(0o755)
    config = WorkerConfig(
        adapter="cli",
        command="bash ok.sh --model {model} --effort {effort}",
        model="tiny-1",
        effort="high",
        attempts=1,
        label="fake-agent",
    )
    outcome = run_task(project / "tasks", "widget", config, root=project)
    assert outcome.succeeded
    text = (project / "results" / "workers" / "widget" / "attempt-01.log").read_text("utf-8")
    assert "--model tiny-1" in text
    assert "{model}" not in text


# ---------------------------------------------------------------------------
# guard primitives


def test_changed_paths_detects_edits_additions_and_removals() -> None:
    before = {"a": "1", "b": "2"}
    after = {"a": "9", "c": "3"}
    assert guard.changed_paths(before, after) == ["a", "b", "c"]


def test_repo_changes_outside_git_is_empty(tmp_path: Path) -> None:
    """Not a git repo: degrade to watching the harness, never crash the run."""
    assert guard.repo_changes(tmp_path) == (set(), set())


# ---------------------------------------------------------------------------
# executor: planner — some work should never go through a Worker


MAIN_TASK_YAML = TASK_YAML.replace("  status: todo", "  executor: main\n  status: todo")


def test_main_worker_modules_are_never_delegated(tmp_path: Path) -> None:
    """The Planner is the Main Worker; what it kept is not the queue's to run."""
    (tmp_path / "tasks").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tasks" / "widget.task.yaml").write_text(MAIN_TASK_YAML, encoding="utf-8")

    # A command that would definitely succeed, to prove it was never invoked.
    outcome = run_task(
        tmp_path / "tasks",
        "widget",
        _config(tmp_path, "touch src/widget.py"),
        root=tmp_path,
    )
    assert outcome.status == "needs_planner"
    assert not outcome.attempts, "no Worker should have been spawned"
    assert not (tmp_path / "src" / "widget.py").exists()
    assert "executor: main" in outcome.message
