"""Tests for worker tasks: materialization, lifecycle, board."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness import task as task_mod
from harness.plan import load_plan
from harness.task import TaskError


@pytest.fixture()
def demo_plan() -> object:
    return load_plan("plans/demo-pipeline.yaml")


def test_materialize_creates_task_files(demo_plan, tmp_path: Path) -> None:
    written = task_mod.materialize(demo_plan, tmp_path / "tasks")
    assert [p.name for p in written] == ["data-gen.task.yaml", "stats.task.yaml"]
    task = task_mod.load_task(tmp_path / "tasks", "stats")
    assert task.plan == "demo-pipeline"
    assert task.depends_on == ["data-gen"]
    assert task.status == "todo"
    assert task.brief.startswith("Implement `src/demo_pipeline/stats.py`")
    assert len(task.acceptance) == 2  # prepare-input + run-stats


def test_materialize_skips_existing(demo_plan, tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    task_mod.materialize(demo_plan, tasks_dir)
    # Simulate a worker claiming a task.
    task_mod.claim(tasks_dir, "data-gen", "worker-x")
    written = task_mod.materialize(demo_plan, tasks_dir)
    assert written == []  # nothing overwritten
    assert task_mod.load_task(tasks_dir, "data-gen").status == "in_progress"


def test_materialize_force_refreshes_spec_but_keeps_lifecycle(demo_plan, tmp_path: Path) -> None:
    """--force re-syncs the spec from the plan; it must never erase the board."""
    tasks_dir = tmp_path / "tasks"
    task_mod.materialize(demo_plan, tasks_dir)
    task_mod.claim(tasks_dir, "data-gen", "worker-x")
    # Corrupt the stored spec so we can prove it gets refreshed.
    stale = task_mod.load_task(tasks_dir, "data-gen")
    stale.brief = "stale brief"
    task_mod.save_task(stale)

    task_mod.materialize(demo_plan, tasks_dir, force=True)

    task = task_mod.load_task(tasks_dir, "data-gen")
    assert task.brief.startswith("Implement `src/demo_pipeline/data_gen.py`")  # refreshed
    assert task.status == "in_progress"  # lifecycle preserved
    assert task.worker == "worker-x"
    assert any("claimed by worker-x" in line for line in task.log)  # audit trail intact
    assert any("re-materialized" in line for line in task.log)


def test_claim_rejects_unready_dependencies(demo_plan, tmp_path: Path) -> None:
    """A Worker must not claim a task whose dependencies are unfinished."""
    tasks_dir = tmp_path / "tasks"
    task_mod.materialize(demo_plan, tasks_dir)
    with pytest.raises(TaskError, match="dependencies not done"):
        task_mod.claim(tasks_dir, "stats", "agent-1")
    # The gate is an override, not a wall — the bypass is recorded.
    task = task_mod.claim(tasks_dir, "stats", "agent-1", force=True)
    assert task.status == "in_progress"
    assert any("--force" in line for line in task.log)


def test_verify_task_fails_on_missing_deliverable(demo_plan, tmp_path: Path) -> None:
    """Passing acceptance is not enough: declared deliverables must exist."""
    tasks_dir = tmp_path / "tasks"
    task_mod.materialize(demo_plan, tasks_dir)
    task = task_mod.load_task(tasks_dir, "data-gen")
    task.deliverables = ["src/demo_pipeline/data_gen.py", "src/demo_pipeline/ghost.py"]

    result = task_mod.verify_task(task, root=".", results_dir=tmp_path / "results")

    assert not result.success
    details = [c.detail for s in result.steps for c in s.checks if not c.passed]
    assert any("ghost.py" in d for d in details)


def test_claim_lifecycle(tmp_path: Path, demo_plan) -> None:
    tasks_dir = tmp_path / "tasks"
    task_mod.materialize(demo_plan, tasks_dir)
    task = task_mod.claim(tasks_dir, "data-gen", "agent-1")
    assert task.status == "in_progress"
    assert task.worker == "agent-1"
    assert task.log  # audit trail recorded
    # Double-claim is rejected.
    with pytest.raises(TaskError, match="in_progress"):
        task_mod.claim(tasks_dir, "data-gen", "agent-2")


def test_block_and_reclaim(tmp_path: Path, demo_plan) -> None:
    tasks_dir = tmp_path / "tasks"
    task_mod.materialize(demo_plan, tasks_dir)
    task_mod.claim(tasks_dir, "data-gen", "agent-1")
    task = task_mod.block(tasks_dir, "data-gen", "contract ambiguous")
    assert task.status == "blocked"
    # Blocked tasks can be reclaimed.
    task = task_mod.claim(tasks_dir, "data-gen", "agent-2")
    assert task.status == "in_progress"


@pytest.mark.skipif(not Path("src/demo_pipeline").exists(), reason="shipped demo required")
def test_demo_task_verify_e2e(tmp_path: Path, demo_plan) -> None:
    """The shipped data-gen task's acceptance passes end-to-end."""
    tasks_dir = tmp_path / "tasks"
    task_mod.materialize(demo_plan, tasks_dir)
    task = task_mod.load_task(tasks_dir, "data-gen")
    result = task_mod.verify_task(task, root=".", results_dir=tmp_path / "results")
    assert result.success, [c.detail for s in result.steps for c in s.checks]


def test_complete_marks_done(tmp_path: Path, demo_plan) -> None:
    tasks_dir = tmp_path / "tasks"
    task_mod.materialize(demo_plan, tasks_dir)
    task_mod.claim(tasks_dir, "data-gen", "agent-1")
    task, result = task_mod.complete(tasks_dir, "data-gen", worker="agent-1")
    assert result.success
    assert task.status == "done"
    assert task.log[-1].endswith("done")
    # Done tasks cannot be claimed again.
    with pytest.raises(TaskError, match="done"):
        task_mod.claim(tasks_dir, "data-gen", "agent-2")


def test_board_and_ready(tmp_path: Path, demo_plan) -> None:
    tasks_dir = tmp_path / "tasks"
    task_mod.materialize(demo_plan, tasks_dir)
    board = task_mod.load_board(tasks_dir)
    assert [t.id for t in board] == ["data-gen", "stats"]
    # data-gen has no deps → ready; stats waits on data-gen.
    assert task_mod.ready_task_ids(board) == ["data-gen"]
    task_mod.claim(tasks_dir, "data-gen", "w1")
    task_mod.complete(tasks_dir, "data-gen", worker="w1")
    board = task_mod.load_board(tasks_dir)
    assert task_mod.ready_task_ids(board) == ["stats"]
    # With its dependency done, stats is now claimable without --force.
    assert task_mod.claim(tasks_dir, "stats", "w2").status == "in_progress"


def test_load_missing_task_raises(tmp_path: Path) -> None:
    with pytest.raises(TaskError, match="not found"):
        task_mod.load_task(tmp_path, "ghost")


def test_invalid_status_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.task.yaml"
    path.write_text("task:\n  id: x\n  status: finished\n", encoding="utf-8")
    with pytest.raises(TaskError, match="invalid status"):
        task_mod.load_task(tmp_path, "bad")
