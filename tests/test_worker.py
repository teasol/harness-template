"""Tests for worker adapters and the retry loop.

The "worker" here is a plain shell command, so the loop is exercised end to
end without any model, network, or API key. That is the point of the adapter
boundary: the harness only knows how to invoke a command and judge the result.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from harness import worker as worker_mod
from harness.task import load_task
from harness.worker import WorkerConfig, WorkerError, load_worker_config, run_task

TASK_YAML = """\
task:
  id: widget
  plan: mini
  title: Build the widget
  depends_on: []
  brief: |
    Create src/widget.py.
  contract:
    inputs:
    - name: seed
      type: int
      description: RNG seed
    outputs: []
  deliverables:
  - src/widget.py
  constraints:
  - stdlib only
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
    """A minimal project with one task whose deliverable does not exist yet."""
    (tmp_path / "tasks").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tasks" / "widget.task.yaml").write_text(TASK_YAML, encoding="utf-8")
    return tmp_path


def _config(project: Path, command: str, attempts: int = 6) -> WorkerConfig:
    return WorkerConfig(adapter="cli", command=command, attempts=attempts, label="fake-agent")


# ---------------------------------------------------------------------------
# configuration


def test_default_config_is_manual(tmp_path: Path) -> None:
    """No config, no API key, still usable — the template must work out of the box."""
    config = load_worker_config(root=tmp_path)
    assert config.adapter == "manual"
    assert config.attempts == worker_mod.DEFAULT_ATTEMPTS == 6


def test_cli_adapter_requires_a_command() -> None:
    with pytest.raises(WorkerError, match="requires a 'command'"):
        WorkerConfig.from_dict({"adapter": "cli"})


def test_unknown_adapter_rejected() -> None:
    with pytest.raises(WorkerError, match="unknown worker adapter"):
        WorkerConfig.from_dict({"adapter": "telepathy"})


def test_attempts_must_be_positive() -> None:
    with pytest.raises(WorkerError, match="positive integer"):
        WorkerConfig.from_dict({"adapter": "manual", "attempts": 0})


def test_config_is_loaded_from_yaml(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "worker.yaml").write_text(
        "worker:\n  adapter: cli\n  command: 'true'\n  attempts: 2\n", encoding="utf-8"
    )
    config = load_worker_config(root=tmp_path)
    assert config.adapter == "cli" and config.attempts == 2


def test_missing_explicit_config_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(WorkerError, match="not found"):
        load_worker_config("configs/nope.yaml", root=tmp_path)


# ---------------------------------------------------------------------------
# briefing


def test_brief_carries_everything_a_worker_needs(project: Path) -> None:
    task = load_task(project / "tasks", "widget")
    brief = worker_mod.build_brief(task, project)
    assert "task 'widget'" in brief
    assert "Create src/widget.py" in brief  # the brief itself
    assert "src/widget.py" in brief  # deliverables
    assert "stdlib only" in brief  # constraints
    assert "seed: int" in brief  # contract
    assert "test -f src/widget.py" in brief  # definition of done


# ---------------------------------------------------------------------------
# the retry loop


def test_manual_adapter_stops_for_a_human(project: Path) -> None:
    outcome = run_task(project / "tasks", "widget", WorkerConfig(), root=project)
    assert outcome.status == "needs_human"
    assert Path(outcome.brief_path).is_file()
    assert load_task(project / "tasks", "widget").status == "todo"


def test_worker_that_succeeds_marks_the_task_done(project: Path) -> None:
    outcome = run_task(
        project / "tasks",
        "widget",
        _config(project, "touch src/widget.py"),
        root=project,
    )
    assert outcome.succeeded
    assert len(outcome.attempts) == 1
    task = load_task(project / "tasks", "widget")
    assert task.status == "done"
    assert task.worker == "fake-agent"


def test_failing_worker_is_retried_with_the_real_output(project: Path) -> None:
    """Retry keeps the same worker and hands it the failure, rather than restarting."""
    script = project / "flaky.sh"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            brief=$(cat)
            echo "$brief" > "last-prompt.txt"
            echo x >> attempts.txt
            if [ "$(wc -l < attempts.txt)" -ge 3 ]; then touch src/widget.py; fi
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)

    outcome = run_task(project / "tasks", "widget", _config(project, "bash flaky.sh"), root=project)

    assert outcome.succeeded
    assert len(outcome.attempts) == 3
    assert [a.passed for a in outcome.attempts] == [False, False, True]
    # The last prompt the worker saw was retry feedback, not the original brief.
    prompt = (project / "last-prompt.txt").read_text(encoding="utf-8")
    assert "did not pass acceptance" in prompt
    assert "attempt 3 of 6" in prompt
    assert "Fix the code; do not start over" in prompt
    # Every attempt is on the record.
    log = "\n".join(load_task(project / "tasks", "widget").log)
    assert "attempt 1/6" in log and "attempt 3/6" in log


def test_attempt_cap_blocks_the_task_for_the_planner(project: Path) -> None:
    """A wedged worker must stop burning budget and hand control back."""
    outcome = run_task(
        project / "tasks",
        "widget",
        _config(project, "echo 'I did nothing useful'", attempts=3),
        root=project,
    )
    assert outcome.status == "failed"
    assert len(outcome.attempts) == 3
    task = load_task(project / "tasks", "widget")
    assert task.status == "blocked"
    assert any("exhausted 3 attempts" in line for line in task.log)


def test_deliverables_are_still_enforced(project: Path) -> None:
    """A worker whose acceptance passes but leaves no deliverable has not delivered."""
    task_file = project / "tasks" / "widget.task.yaml"
    # Acceptance that always passes, but the deliverable is still declared.
    task_file.write_text(
        TASK_YAML.split("  acceptance:")[0]
        + "  acceptance:\n    steps:\n    - id: check\n      run: 'true'\n"
        + "  status: todo\n  worker: null\n  log: []\n",
        encoding="utf-8",
    )
    outcome = run_task(
        project / "tasks", "widget", _config(project, "true", attempts=2), root=project
    )
    assert outcome.status == "failed"
    assert load_task(project / "tasks", "widget").status == "blocked"


def test_unrunnable_command_reports_an_error(project: Path) -> None:
    outcome = run_task(
        project / "tasks",
        "widget",
        _config(project, "definitely-not-a-real-binary-xyz", attempts=2),
        root=project,
    )
    # The shell reports the failure; the harness must not mark the task done.
    assert not outcome.succeeded
    assert load_task(project / "tasks", "widget").status != "done"


def test_worker_report_records_only_observed_facts(project: Path) -> None:
    outcome = run_task(
        project / "tasks", "widget", _config(project, "touch src/widget.py"), root=project
    )
    path = worker_mod.write_worker_report(outcome, "results", root=project)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["adapter"] == "cli"
    assert payload["attempts"][0]["exit_code"] == 0
    # Cost is never estimated — the harness says plainly that it does not know.
    assert "not measured" in payload["cost"]


def test_brief_reaches_the_command_as_a_file_too(project: Path) -> None:
    """Agents that take a prompt argument (not stdin) must work as well.

    Both paths are advertised in configs/worker.yaml, so both are tested.
    """
    script = project / "argtool.sh"
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            prompt="$1"
            case "$prompt" in
              *"Create src/widget.py"*) touch src/widget.py ;;
            esac
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)

    outcome = run_task(
        project / "tasks",
        "widget",
        _config(project, 'bash argtool.sh "$(cat {brief_file})"'),
        root=project,
    )

    assert outcome.succeeded, [a.detail for a in outcome.attempts]
    assert (project / "src" / "widget.py").is_file()


def test_root_placeholder_is_substituted(project: Path) -> None:
    outcome = run_task(
        project / "tasks",
        "widget",
        _config(project, "touch {root}/src/widget.py"),
        root=project,
    )
    assert outcome.succeeded
