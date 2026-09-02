"""Tests for the progress heartbeat.

The motivating case: a Worker attempt ran to its 1800s cap with no output, no
elapsed time, and no way to tell a working agent from a wedged one — because
both the step runner and the Worker adapter buffer a child's output until it
exits. A plan is serial, so the position is always knowable; these tests check
that it is actually published while the work is in flight, not after.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from harness.orchestrate.worker import WorkerConfig, run_task
from harness.verify import checklist, heartbeat

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
  checklist:
  - name: check
    run: test -f src/widget.py
  executor: sub
  status: todo
  worker: null
  log: []
"""


# ---------------------------------------------------------------------------
# formatting and staleness


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (45, "45s"), (60, "1m00s"), (750, "12m30s"), (3600, "1h00m"), (7530, "2h05m")],
)
def test_durations_read_at_a_glance(seconds: float, expected: str) -> None:
    assert heartbeat.human_duration(seconds) == expected


def test_missing_heartbeat_reads_as_nothing_running(tmp_path: Path) -> None:
    assert heartbeat.read(tmp_path) is None


def test_corrupt_heartbeat_reads_as_nothing_running(tmp_path: Path) -> None:
    """A half-written file must not crash the watcher that came to help."""
    heartbeat.heartbeat_path(tmp_path).write_text("{ not json", encoding="utf-8")
    assert heartbeat.read(tmp_path) is None


def test_a_stopped_ticker_is_reported_as_dead_not_slow(tmp_path: Path) -> None:
    """The distinction that matters during a long wait."""
    heartbeat.heartbeat_path(tmp_path).write_text(
        json.dumps(
            {
                "activity": "worker",
                "label": "widget",
                "position": "attempt 2/6",
                "started_at": "2020-01-01T00:00:00Z",
                "updated_at": "2020-01-01T00:00:00Z",
                "timeout_s": 1800,
                "pid": 1,
                "detail": {},
            }
        ),
        encoding="utf-8",
    )
    beat = heartbeat.read(tmp_path)
    assert beat is not None
    assert beat.is_stale
    assert "NO HEARTBEAT" in beat.describe()


def test_describe_names_position_elapsed_and_the_cap(tmp_path: Path) -> None:
    with heartbeat.Beat(tmp_path, "worker", "widget", "module 1/2 · attempt 2/6", timeout_s=1800):
        beat = heartbeat.read(tmp_path)
        assert beat is not None
        text = beat.describe()
        assert "worker widget" in text
        assert "module 1/2 · attempt 2/6" in text
        assert "before the cap" in text
        assert not beat.is_stale


def test_beat_clears_itself_on_exit(tmp_path: Path) -> None:
    with heartbeat.Beat(tmp_path, "step", "probe"):
        assert heartbeat.read(tmp_path) is not None
    assert heartbeat.read(tmp_path) is None


def test_beat_clears_itself_even_when_the_work_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), heartbeat.Beat(tmp_path, "step", "probe"):
        raise RuntimeError("step blew up")
    assert heartbeat.read(tmp_path) is None


def test_ticker_keeps_the_timestamp_moving(tmp_path: Path) -> None:
    """Liveness is the whole point: a frozen timestamp means nobody is home.

    Timestamps have second resolution, so proving movement needs more than a
    second of sleep — asserting on sub-second slack instead just makes the test
    fail on a loaded machine without testing anything more.
    """
    with heartbeat.Beat(tmp_path, "step", "slow", tick_seconds=0.05):
        first = heartbeat.read(tmp_path)
        time.sleep(1.3)
        second = heartbeat.read(tmp_path)
    assert first is not None and second is not None
    assert second.updated_at > first.updated_at, "the ticker must actually advance it"
    assert not second.is_stale


def test_a_broken_results_dir_never_breaks_the_run(tmp_path: Path) -> None:
    """Describing the work must never be able to fail the work."""
    blocker = tmp_path / "results"
    blocker.write_text("I am a file, not a directory", encoding="utf-8")
    with heartbeat.Beat(blocker / "nested", "step", "probe"):
        pass  # must not raise


def _watch_for(results: Path, label: str, timeout: float = 8.0) -> list[heartbeat.Heartbeat]:
    """Poll until the named leg is in flight, in a thread, and return what was seen.

    A fixed sleep races the work it is watching: too early and the step has not
    started, too late and it has finished. Polling for a condition does not.
    """
    seen: list[heartbeat.Heartbeat] = []

    def watch() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            beat = heartbeat.read(results)
            if beat is not None and beat.label == label:
                seen.append(beat)
                return
            time.sleep(0.02)

    thread = threading.Thread(target=watch)
    thread.start()
    return seen, thread


# ---------------------------------------------------------------------------
# published from the real code paths


def test_a_running_item_is_visible_from_outside(tmp_path: Path) -> None:
    """The case that motivated this: work in flight, output buffered, silence."""
    items = [
        checklist.Item(name="quick", run="true", module="slow"),
        checklist.Item(name="slow-one", run="sleep 1.5", module="slow"),
    ]
    results = tmp_path / "results"
    seen, watcher = _watch_for(results, "slow:slow-one")
    run = checklist.run_items(items, root=tmp_path, results_dir=results, scope="slow")
    watcher.join()

    assert run.passed
    assert seen, "an item running for 1.5s must be observable while it runs"
    beat = seen[0]
    assert beat.activity == "check"
    assert beat.label == "slow:slow-one"
    assert beat.position == "2/2", "the position within the checklist must be reported"
    # And it is gone once the run finishes.
    assert heartbeat.read(results) is None


def test_a_running_worker_attempt_is_visible_from_outside(tmp_path: Path) -> None:
    (tmp_path / "tasks").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tasks" / "widget.task.yaml").write_text(TASK_YAML, encoding="utf-8")
    script = tmp_path / "slow.sh"
    script.write_text(
        "#!/usr/bin/env bash\ncat > /dev/null\nsleep 1.5\ntouch src/widget.py\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    results = tmp_path / "results"
    lines: list[str] = []
    seen, watcher = _watch_for(results, "widget")
    outcome = run_task(
        tmp_path / "tasks",
        "widget",
        WorkerConfig(adapter="cli", command="bash slow.sh", attempts=2, label="fake"),
        root=tmp_path,
        results_dir=results,
        position=(1, 2),
        progress=lines.append,
    )
    watcher.join()

    assert outcome.succeeded
    assert seen, "a Worker attempt must be observable while it runs"
    beat = seen[0]
    assert beat.activity == "worker"
    assert beat.label == "widget"
    assert "module 1/2" in beat.position
    assert "attempt 1/2" in beat.position

    # The blocked terminal is told too, at the start rather than at the end.
    assert any("attempt 1/2 started" in line for line in lines)
    assert any("checklist passed" in line for line in lines)


def test_progress_command_reports_nothing_running(tmp_path: Path, capsys) -> None:
    from harness.cli import main

    assert main(["progress", "--root", str(tmp_path)]) == 1
    assert "Nothing running." in capsys.readouterr().out


def test_progress_command_renders_a_live_beat(tmp_path: Path, capsys) -> None:
    from harness.cli import main

    (tmp_path / "results").mkdir()
    with heartbeat.Beat(tmp_path / "results", "worker", "widget", "attempt 2/6", timeout_s=1800):
        assert main(["progress", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "worker widget" in out
    assert "attempt 2/6" in out


def test_heartbeat_survives_a_real_subprocess_boundary(tmp_path: Path) -> None:
    """The reader is usually a different process, so check across one."""
    (tmp_path / "results").mkdir()
    with heartbeat.Beat(tmp_path / "results", "step", "probe", "1/1"):
        proc = subprocess.run(
            [
                "python3",
                "-c",
                "import json,sys;print(json.load(open(sys.argv[1]))['label'])",
                str(heartbeat.heartbeat_path(tmp_path / "results")),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    assert proc.stdout.strip() == "probe"
