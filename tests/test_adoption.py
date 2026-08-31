"""Tests for arriving in a codebase that already exists.

Most harness projects do not start empty, and until now that looked identical
to one that did: `harness init` printed the same next steps either way, so the
one fact that matters on day one — that none of this code is verified yet —
went unsaid.

What is deliberately *not* tested here is a prescribed modularization
procedure, because there isn't one. Deciding the decomposition is the Planner's
job; these tests check that the Planner is put in a position to do it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness import adoption


@pytest.fixture()
def existing_project(tmp_path: Path) -> Path:
    """A repository with code in it, and no harness."""
    (tmp_path / "src").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "src" / "model.py").write_text("def score(x): return x\n", encoding="utf-8")
    (tmp_path / "src" / "pipeline.py").write_text("def go(): pass\n", encoding="utf-8")
    (tmp_path / "scripts" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# project\n", encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "existing code"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


# ---------------------------------------------------------------------------
# detection


def test_an_empty_project_is_not_an_adoption(tmp_path: Path) -> None:
    assert adoption.record(tmp_path) is None
    assert adoption.read(tmp_path) is None


def test_existing_source_is_counted_with_evidence(existing_project: Path) -> None:
    """A count alone is not checkable; the samples make the record verifiable."""
    result = adoption.record(existing_project)
    assert result is not None
    assert result.is_adoption
    assert result.source_files == 3
    assert "src/model.py" in result.samples
    assert result.commit, "the arrival point must be pinned to a commit"


def test_the_harness_does_not_count_itself(existing_project: Path) -> None:
    """Otherwise every project looks like an adoption of the harness's own code."""
    harness_dir = existing_project / ".harness" / "agents"
    harness_dir.mkdir(parents=True)
    (harness_dir / "planner.md").write_text("role", encoding="utf-8")
    (existing_project / ".harness" / "scripts").mkdir()
    (existing_project / ".harness" / "scripts" / "demo.py").write_text("x=1", encoding="utf-8")
    count, _ = adoption.count_existing_source(existing_project)
    assert count == 3


def test_build_and_cache_directories_are_ignored(existing_project: Path) -> None:
    for noise in ("__pycache__", "node_modules", ".venv", "build"):
        directory = existing_project / noise
        directory.mkdir()
        (directory / "junk.py").write_text("x=1", encoding="utf-8")
    count, _ = adoption.count_existing_source(existing_project)
    assert count == 3


def test_a_corrupt_marker_reads_as_absent(existing_project: Path) -> None:
    adoption.marker_path(existing_project).parent.mkdir(parents=True, exist_ok=True)
    adoption.marker_path(existing_project).write_text("{ not json", encoding="utf-8")
    assert adoption.read(existing_project) is None


def test_the_marker_round_trips(existing_project: Path) -> None:
    written = adoption.record(existing_project)
    assert written is not None
    loaded = adoption.read(existing_project)
    assert loaded is not None
    assert loaded.source_files == written.source_files
    assert loaded.commit == written.commit
    assert json.loads(adoption.marker_path(existing_project).read_text())["source_files"] == 3


# ---------------------------------------------------------------------------
# what the flow tells you


def test_init_names_the_adoption_path(existing_project: Path, capsys) -> None:
    """install → init → project → planner → work, and init has to start it."""
    from harness.cli import main

    assert main(["init", str(existing_project), "--no-setup"]) == 0
    out = capsys.readouterr().out
    assert "already has 3 source file(s)" in out
    assert "none of" in out
    # It points at a Planner rather than at a procedure.
    assert "harness create -n" in out
    assert "harness project init" in out
    # And it does not hand the user the Planner's own command: nobody starts a
    # plan by hand here.
    assert "harness plan new" not in out
    assert "tell that Planner what you want done" in out


def test_init_on_an_empty_project_keeps_the_greenfield_path(tmp_path: Path, capsys) -> None:
    from harness.cli import main

    assert main(["init", str(tmp_path), "--no-setup"]) == 0
    out = capsys.readouterr().out
    # The command itself, not a label describing it: it is the first thing to run.
    assert "create -n <planner-name>" in out
    assert "already has" not in out
    # The demo stays reachable, just off the main path.
    assert "configs/demo.yaml" in out


def test_next_steps_drop_what_is_already_done(existing_project: Path) -> None:
    """The list is a state read, not a fixed script."""
    from harness import planners as planners_mod
    from harness import project as project_mod

    (existing_project / ".harness" / "configs").mkdir(parents=True, exist_ok=True)
    steps = adoption.next_steps(existing_project)
    assert any("project init" in s for s in steps)
    assert any("harness create -n" in s for s in steps)

    project_mod.write_template(existing_project)
    planners_mod.create("owner", model="m", root=existing_project)
    steps = adoption.next_steps(existing_project)
    assert not any("project init" in s for s in steps)
    assert not any("harness create -n" in s for s in steps)
    # Nothing left for the person to run: the next move is a conversation with
    # the Planner, which is what THEN_TALK says.
    assert steps == []
    assert "starts it itself" in adoption.THEN_TALK


# ---------------------------------------------------------------------------
# what the Planner is handed


def test_the_briefing_states_the_situation_and_refuses_to_prescribe() -> None:
    record = adoption.Adoption(
        adopted_at="2026-08-30T00:00:00Z",
        commit="a8ea185f17dabeef",
        source_files=42,
        samples=["src/model.py", "src/pipeline.py"],
    )
    text = "\n".join(adoption.brief_lines(record))

    assert "predates the harness" in text
    assert "42 existing source file(s)" in text
    assert "a8ea185f17da" in text, "unverified needs a concrete boundary"

    # The decomposition is the Planner's call, and the briefing must say so.
    assert "yours to decide" in text
    assert "nothing here is a" in text

    # The five conditions are enforceable facts about the harness, so they stay.
    for title, _ in adoption.BOUNDARY_CONDITIONS:
        assert title in text
    assert len(adoption.BOUNDARY_CONDITIONS) == 5

    # And the one ordering principle that is expensive to learn late.
    assert "pin the behaviour you must not" in text
    assert "did not move" in text
    assert "tests import" in text and "entry point runs" in text
