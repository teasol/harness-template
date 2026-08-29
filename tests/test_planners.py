"""Tests for Planners that outlive one experiment.

A Planner spends its first hour learning a project — where the numbers of
record live, which interpreter has the dependencies, which arms are closed.
Discarding that at the end of every experiment means paying the hour again and
repeating the same first-time mistakes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from harness import planners as planners_mod
from harness.planners import PlannerError


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_a_planner_needs_a_model(repo: Path) -> None:
    """Two runs planned by different models are not the same experiment."""
    with pytest.raises(PlannerError, match="needs --model"):
        planners_mod.create("icf", model="", root=repo)


def test_invalid_names_rejected(repo: Path) -> None:
    with pytest.raises(PlannerError, match="invalid planner name"):
        planners_mod.create("ICF Planner", model="m", root=repo)


def test_create_load_and_list(repo: Path) -> None:
    planners_mod.create("icf", model="claude-opus-5", effort="high", root=repo)
    planner = planners_mod.load("icf", root=repo)
    assert planner.model == "claude-opus-5"
    assert planner.effort == "high"
    assert planner.created_at
    assert [p.name for p in planners_mod.list_planners(repo)] == ["icf"]
    with pytest.raises(PlannerError, match="already exists"):
        planners_mod.create("icf", model="other", root=repo)


def test_unknown_planner_names_the_known_ones(repo: Path) -> None:
    planners_mod.create("icf", model="m", root=repo)
    with pytest.raises(PlannerError, match="known: \\['icf'\\]"):
        planners_mod.load("nope", root=repo)


def test_notes_accumulate_and_carry_the_experiment(repo: Path) -> None:
    planners_mod.create("icf", model="m", root=repo)
    planners_mod.add_note("icf", "ICF_CKPT is empty here and that is correct.", "baseline", repo)
    planners_mod.add_note("icf", "The node is A5000, not B200.", "baseline", repo)
    planner = planners_mod.load("icf", root=repo)
    assert len(planner.notes) == 2
    assert planner.notes[0].experiment == "baseline"
    assert planner.notes[0].at
    with pytest.raises(PlannerError, match="needs text"):
        planners_mod.add_note("icf", "   ", root=repo)


def test_linking_an_experiment_is_idempotent(repo: Path) -> None:
    planners_mod.create("icf", model="m", root=repo)
    planners_mod.link_experiment("icf", "baseline", root=repo)
    planners_mod.link_experiment("icf", "baseline", root=repo)
    planners_mod.link_experiment("icf", "ct-sweep", root=repo)
    assert planners_mod.load("icf", root=repo).experiments == ["baseline", "ct-sweep"]


def test_registry_is_shared_across_worktrees(repo: Path) -> None:
    """A worktree must append to the same memory, not to a copy that dies with it.

    Experiments live in worktrees; a Planner spans experiments. If the registry
    resolved per-worktree, every experiment would start from nothing again —
    which is the problem this exists to solve.
    """
    planners_mod.create("icf", model="m", root=repo)
    worktree = repo / ".experiments" / "baseline"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "exp/baseline", str(worktree)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    # Seen from inside the worktree...
    assert planners_mod.exists("icf", root=worktree)
    planners_mod.add_note("icf", "learned in the worktree", "baseline", root=worktree)
    # ...and written back to the one registry.
    assert len(planners_mod.load("icf", root=repo).notes) == 1
    assert planners_mod.planner_path("icf", worktree) == planners_mod.planner_path("icf", repo)


def test_brief_carries_notes_forward_with_a_caveat(repo: Path) -> None:
    planners_mod.create("icf", model="claude-opus-5", effort="high", root=repo)
    planners_mod.link_experiment("icf", "baseline", root=repo)
    planners_mod.add_note("icf", "The node is A5000, not B200.", "baseline", repo)
    text = "\n".join(planners_mod.brief_lines(planners_mod.load("icf", root=repo), "ct-sweep"))

    assert "You are **icf** (claude-opus-5, effort high)" in text
    assert "A5000" in text
    # Stale notes are the obvious failure mode of carrying anything forward.
    assert "Verify anything that names a file" in text
    assert "planner note icf --experiment ct-sweep" in text


def test_brief_of_a_first_run_says_so(repo: Path) -> None:
    planners_mod.create("icf", model="m", root=repo)
    text = "\n".join(planners_mod.brief_lines(planners_mod.load("icf", root=repo)))
    assert "first run" in text


def test_no_planner_means_no_section(repo: Path) -> None:
    assert planners_mod.brief_lines(None) == []
