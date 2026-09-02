"""Tests for handing a project over to a session that was not there.

The harness assumed one Planner session per project. Sessions end for reasons
that have nothing to do with the work: the context fills, the laptop closes, the
next day is spent on a different machine with a different tool. Each of those
produces a session that knows nothing, and the only thing that scales is a
document it can be pointed at.

Two failures are gated here. The first is a handoff that says nothing, because
recording was a ritual for the end of a run and no run ends tidily. The second
is worse and was silent: a fresh clone of a project with work in flight looked
*identical* to a project with no work at all, because plans were discovered by
walking `git worktree list`, and the harness told the reader to start a new plan
beside the one that already had the history.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from harness import init as init_mod
from harness.handoff import document as handoff_mod
from harness.handoff import planners as planners_mod
from harness.orchestrate import plans as plans_mod

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="git required")


def _run(cwd: Path, *args: str) -> str:
    proc = subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)
    return proc.stdout.strip()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """An initialized project with one commit, the way a real one starts."""
    root = tmp_path / "home"
    root.mkdir()
    init_mod.init_project(root)
    _run(root, "git", "init", "--quiet", ".")
    _run(root, "git", "config", "user.email", "test@example.invalid")
    _run(root, "git", "config", "user.name", "test")
    _run(root, "git", "add", "-A")
    _run(root, "git", "commit", "--quiet", "-m", "init")
    return root


# ---------------------------------------------------------------------------
# the document


def test_a_handoff_carries_what_no_file_records(project: Path) -> None:
    """Derived state is the cheap half; the reasoning is why this exists."""
    planners_mod.create("dasol", model="m", root=project)
    planners_mod.add_note(
        "dasol", "chose single process: mp broke the seed", kind="decision", root=project
    )
    planners_mod.add_note(
        "dasol", "the loader only reads v2 fixtures", kind="dead-end", root=project
    )
    planners_mod.add_note("dasol", "numbers of record live in docs/status.md", root=project)

    text = handoff_mod.render(project)
    assert "mp broke the seed" in text
    assert "only reads v2 fixtures" in text
    assert "docs/status.md" in text
    # Read in the order they are needed: what not to retry outranks trivia.
    assert text.index("Decisions already made") < text.index("What else is known")
    assert text.index("Dead ends") < text.index("What else is known")


def test_only_the_last_intent_is_shown(project: Path) -> None:
    """Intent is state, not history — an old one reads as a current instruction."""
    planners_mod.create("dasol", root=project)
    planners_mod.add_note("dasol", "writing the parser contract", kind="next", root=project)
    planners_mod.add_note("dasol", "building the widget module", kind="next", root=project)

    text = handoff_mod.render(project)
    assert "building the widget module" in text
    assert "writing the parser contract" not in text


def test_an_empty_handoff_says_it_is_empty(project: Path) -> None:
    """Silence would read as 'nothing to know', which is the opposite."""
    text = handoff_mod.render(project)
    assert "Nothing has been recorded yet" in text


def test_notes_from_every_planner_are_carried(project: Path) -> None:
    """Switching tool means a new model, so a new Planner — not a new project."""
    planners_mod.create("dasol", model="opus", root=project)
    planners_mod.create("dasol-codex", model="codex", root=project)
    planners_mod.add_note("dasol", "the seed lives in configs/base.yaml", root=project)
    planners_mod.add_note("dasol-codex", "wandb is offline on this node", root=project)

    text = handoff_mod.render(project)
    assert "configs/base.yaml" in text
    assert "wandb is offline" in text
    assert "dasol-codex" in text  # who said it is part of how much it weighs


def test_the_handoff_lands_in_the_main_tree_not_the_worktree(project: Path) -> None:
    """A plan's branch is never merged, so a handoff inside it travels nowhere."""
    work = plans_mod.start("fix-loader", root=project)
    assert handoff_mod.handoff_path(work.path) == project / "HANDOFF.md"

    written = handoff_mod.write(work.path)
    assert written == project / "HANDOFF.md"
    assert not (work.path / "HANDOFF.md").exists()


def test_refresh_never_breaks_its_caller(tmp_path: Path) -> None:
    """`task done` must not fail because a document could not be written."""
    assert handoff_mod.refresh(tmp_path / "does-not-exist") is None


def test_a_reading_list_never_points_at_a_missing_file(project: Path) -> None:
    """Being sent to a path that is not there is worse than being sent nowhere."""
    text = handoff_mod.render(project)
    for line in text.splitlines():
        if line.startswith("- `") and "` — " in line:
            rel = line.split("`")[1]
            assert (project / rel).exists(), rel


# ---------------------------------------------------------------------------
# the machine changed, not the project


@needs_git
def test_a_clone_still_finds_the_plan_that_has_no_worktree(project: Path, tmp_path: Path) -> None:
    """The silent failure: a fresh clone looked like a project with no work."""
    work = plans_mod.start("fix-loader", root=project)
    _run(work.path, "git", "add", "-A")
    _run(work.path, "git", "commit", "--quiet", "-m", "plan scaffold")

    office = tmp_path / "office"
    _run(tmp_path, "git", "clone", "--quiet", str(project), str(office))

    # The old view, and why it was not enough on its own.
    assert plans_mod.list_plans(office) == []
    assert plans_mod.dormant_plans(office) == ["fix-loader"]

    status = plans_mod.project_status(office)
    assert status.dormant == ["fix-loader"]
    assert "no worktree on this machine" in status.headline
    assert any("plan resume fix-loader" in step for step in status.next_steps)


@needs_git
def test_resume_gives_the_plan_a_worktree_without_touching_its_contents(
    project: Path, tmp_path: Path
) -> None:
    """Scaffolding over a resumed plan would destroy the work being handed over."""
    work = plans_mod.start("fix-loader", root=project)
    work.plan_path.write_text("goal: the real plan, not a scaffold\n", encoding="utf-8")
    _run(work.path, "git", "add", "-A")
    _run(work.path, "git", "commit", "--quiet", "-m", "the plan")

    office = tmp_path / "office"
    _run(tmp_path, "git", "clone", "--quiet", str(project), str(office))

    resumed = plans_mod.resume("fix-loader", root=office)
    assert resumed.path == office / ".worktrees" / "fix-loader"
    assert "the real plan, not a scaffold" in resumed.plan_path.read_text(encoding="utf-8")
    assert plans_mod.dormant_plans(office) == []
    assert [item.name for item in plans_mod.list_plans(office)] == ["fix-loader"]


@needs_git
def test_resume_refuses_when_the_plan_is_already_here(project: Path) -> None:
    """Two worktrees for one branch is a git error; say the useful thing first."""
    plans_mod.start("fix-loader", root=project)
    with pytest.raises(plans_mod.WorkPlanError, match="already"):
        plans_mod.resume("fix-loader", root=project)


@needs_git
def test_resume_refuses_a_plan_no_branch_carries(project: Path) -> None:
    with pytest.raises(plans_mod.WorkPlanError, match="no branch"):
        plans_mod.resume("never-existed", root=project)


@needs_git
def test_a_branch_without_a_plan_file_is_not_a_plan(project: Path) -> None:
    """There is no naming scheme to match on, so the plan file is the evidence."""
    _run(project, "git", "branch", "some-refactor")
    assert plans_mod.dormant_plans(project) == []


def test_refresh_writes_nothing_for_a_project_with_nothing_to_hand_over(project: Path) -> None:
    """A document saying "nothing recorded" is noise in someone's working copy.

    It is also the first thing a clone of a template inherits, where it would
    describe a project that is not theirs.
    """
    assert handoff_mod.refresh(project) is None
    assert not (project / "HANDOFF.md").exists()

    planners_mod.create("dasol", root=project)
    assert handoff_mod.refresh(project) is not None


def test_an_existing_handoff_is_always_refreshed(project: Path) -> None:
    """A stale handoff is worse than none: it will be read as current."""
    handoff_mod.write(project)
    stamped = (project / "HANDOFF.md").read_text(encoding="utf-8")
    (project / "HANDOFF.md").write_text("stale\n", encoding="utf-8")

    assert handoff_mod.refresh(project) is not None
    assert (project / "HANDOFF.md").read_text(encoding="utf-8") != "stale\n"
    assert stamped.splitlines()[0] == (project / "HANDOFF.md").read_text().splitlines()[0]
