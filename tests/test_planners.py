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


def test_a_manual_planner_may_be_created_without_a_model(repo: Path) -> None:
    """Requiring it at creation blocked the one tier where it is unknowable.

    A manual Planner is a session a person opens later, so nobody can name its
    model in advance. Knowing it still matters — two runs planned by different
    models are not the same experiment — but the report is where that gets
    insisted on, not creation.
    """
    planner = planners_mod.create("icf", root=repo)
    assert planner.model == ""
    assert planners_mod.load("icf", root=repo).model == ""


def test_the_model_can_be_recorded_once_it_is_known(repo: Path) -> None:
    planners_mod.create("icf", root=repo)
    planners_mod.set_model("icf", "claude-opus-5", effort="high", root=repo)
    planner = planners_mod.load("icf", root=repo)
    assert planner.model == "claude-opus-5"
    assert planner.effort == "high"
    with pytest.raises(PlannerError, match="a model is required"):
        planners_mod.set_model("icf", "  ", root=repo)


def test_setting_a_model_keeps_the_notes(repo: Path) -> None:
    """Filling in the gap must not cost the memory that is the point of a Planner."""
    planners_mod.create("icf", root=repo)
    planners_mod.add_note("icf", "the node is A5000", "baseline", repo)
    planners_mod.link_plan("icf", "baseline", root=repo)
    planners_mod.set_model("icf", "m", root=repo)
    planner = planners_mod.load("icf", root=repo)
    assert [n.text for n in planner.notes] == ["the node is A5000"]
    assert planner.plans == ["baseline"]


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


def test_notes_accumulate_and_carry_the_plan(repo: Path) -> None:
    planners_mod.create("icf", model="m", root=repo)
    planners_mod.add_note("icf", "ICF_CKPT is empty here and that is correct.", "baseline", repo)
    planners_mod.add_note("icf", "The node is A5000, not B200.", "baseline", repo)
    planner = planners_mod.load("icf", root=repo)
    assert len(planner.notes) == 2
    assert planner.notes[0].plan == "baseline"
    assert planner.notes[0].at
    with pytest.raises(PlannerError, match="needs text"):
        planners_mod.add_note("icf", "   ", root=repo)


def test_linking_a_plan_is_idempotent(repo: Path) -> None:
    planners_mod.create("icf", model="m", root=repo)
    planners_mod.link_plan("icf", "baseline", root=repo)
    planners_mod.link_plan("icf", "baseline", root=repo)
    planners_mod.link_plan("icf", "ct-sweep", root=repo)
    assert planners_mod.load("icf", root=repo).plans == ["baseline", "ct-sweep"]


def test_registry_is_shared_across_worktrees(repo: Path) -> None:
    """A worktree must append to the same memory, not to a copy that dies with it.

    Experiments live in worktrees; a Planner spans experiments. If the registry
    resolved per-worktree, every experiment would start from nothing again —
    which is the problem this exists to solve.
    """
    planners_mod.create("icf", model="m", root=repo)
    worktree = repo / ".worktrees" / "baseline"
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
    planners_mod.link_plan("icf", "baseline", root=repo)
    planners_mod.add_note("icf", "The node is A5000, not B200.", "baseline", repo)
    text = "\n".join(planners_mod.brief_lines(planners_mod.load("icf", root=repo), "ct-sweep"))

    assert "You are **icf** (claude-opus-5, effort high)" in text
    assert "A5000" in text
    # Stale notes are the obvious failure mode of carrying anything forward.
    assert "Verify anything that names a file" in text
    assert "planner note icf --plan ct-sweep" in text


def test_brief_of_a_first_run_says_so(repo: Path) -> None:
    planners_mod.create("icf", model="m", root=repo)
    text = "\n".join(planners_mod.brief_lines(planners_mod.load("icf", root=repo)))
    assert "first run" in text


def test_no_planner_means_no_section(repo: Path) -> None:
    assert planners_mod.brief_lines(None) == []


# ---------------------------------------------------------------------------
# the Planner comes before the experiment it owns


def test_status_asks_for_a_planner_and_nothing_else(repo: Path, capsys) -> None:
    """With no Planner registered there is exactly one thing for a person to do.

    Not "create a Planner, then start a plan": starting the work is the
    Planner's move, made after the two of them agree what the work is.
    """
    from harness.cli import main
    from harness.init import init_project

    init_project(repo, name="p")
    main(["status", "--root", str(repo)])
    out = capsys.readouterr().out
    assert "harness create -n" in out
    assert "harness plan new" not in out


def test_status_names_the_planner_once_one_exists(repo: Path, capsys) -> None:
    from harness.cli import main
    from harness.init import init_project

    init_project(repo, name="p")
    planners_mod.create("owner", model="m", root=repo)
    main(["status", "--root", str(repo)])
    out = capsys.readouterr().out
    assert "--planner owner" in out
    assert "planner create" not in out


@pytest.mark.parametrize("registered", [False, True])
def test_plan_new_says_when_its_planner_is_only_a_label(
    repo: Path, capsys, registered: bool
) -> None:
    """An unregistered Planner means no model on record, so say so at the one
    moment it is still cheap to fix."""
    from harness.cli import main
    from harness.init import init_project

    init_project(repo, name="p")
    if registered:
        planners_mod.create("owner", model="claude-opus-5", root=repo)
    assert main(["plan", "new", "e1", "--planner", "owner", "--root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert ("is a label, not a registered Planner" in out) is not registered


def test_create_is_a_top_level_command(repo: Path, capsys) -> None:
    """A Planner is the only thing the harness creates, so `harness create`."""
    from harness.cli import main

    assert main(["create", "-n", "owner", "--model", "claude-opus-5", "--root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Planner 'owner' created (claude-opus-5)" in out
    assert "--planner owner" in out, "it must point at the next step"
    assert planners_mod.load("owner", root=repo).model == "claude-opus-5"


def test_create_refuses_a_duplicate(repo: Path, capsys) -> None:
    from harness.cli import main

    assert main(["create", "-n", "owner", "--model", "m", "--root", str(repo)]) == 0
    assert main(["create", "-n", "owner", "--model", "m", "--root", str(repo)]) == 2
    assert "already exists" in capsys.readouterr().err


def test_create_still_requires_a_name(repo: Path) -> None:
    """`--model` became optional; `-n` did not."""
    from harness.cli import main

    with pytest.raises(SystemExit):
        main(["create", "--model", "m", "--root", str(repo)])


def test_create_without_a_model_under_a_manual_tier(repo: Path, capsys) -> None:
    """The reported bug: manual Planner tier, and `harness create` refused to run."""
    from harness.cli import main
    from harness.init import init_project

    init_project(repo, name="p")  # ships a manual planner tier
    assert main(["create", "-n", "planner-first", "--root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "model not recorded" in out
    assert "expected for a manual Planner" in out
    assert "harness planner set planner-first --model" in out
    assert planners_mod.exists("planner-first", root=repo)


def test_create_inherits_the_configured_planner_model(repo: Path, capsys) -> None:
    """Nobody should retype what `harness setup` already recorded."""
    from harness.cli import main
    from harness.init import init_project
    from harness.setup import build_config, load_platforms, write_agent_config

    init_project(repo, name="p")
    platforms = load_platforms(root=repo)
    tier = build_config(platforms["opencode"], model="deepseek/deepseek-v4-pro", effort="high")
    write_agent_config(tier, tier, root=repo)

    assert main(["create", "-n", "auto", "--root", str(repo)]) == 0
    assert "taken from the Planner tier" in capsys.readouterr().out
    assert planners_mod.load("auto", root=repo).model == "deepseek/deepseek-v4-pro"


def test_planner_set_via_cli(repo: Path, capsys) -> None:
    from harness.cli import main

    planners_mod.create("icf", root=repo)
    assert main(["planner", "set", "icf", "--model", "m-1", "--root", str(repo)]) == 0
    assert "now records m-1" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# a manual Planner has to be handed something


def test_create_prints_a_paste_block_for_a_manual_planner(repo: Path, capsys) -> None:
    """Nobody spawns a manual Planner, so `create` has to give you what to paste."""
    from harness.cli import main
    from harness.init import init_project

    init_project(repo, name="p")  # ships a manual Planner tier
    assert main(["create", "-n", "owner", "--root", str(repo)]) == 0
    out = capsys.readouterr().out

    assert "paste this" in out
    assert 'You are the Planner "owner"' in out
    # Paths, not pasted contracts: the files are long and already authoritative,
    # and a copy in a prompt only drifts from them.
    assert ".harness/agents/planner.md" in out
    assert "AGENTS.md" in out
    assert "harness planner set owner --model" in out
    assert "harness status" in out
    # Short enough to actually paste.
    block = out[out.index("─") :]
    assert len(block.splitlines()) < 20, "the block must stay short"


def test_the_paste_block_only_lists_files_that_exist(repo: Path) -> None:
    """Sending a Planner to a missing path is worse than sending it nowhere."""
    from harness.init import init_project
    from harness.project import write_template

    init_project(repo, name="p")
    planner = planners_mod.create("owner", root=repo)

    without = "\n".join(planners_mod.onboarding_lines(planner, repo))
    assert "project.yaml" not in without

    write_template(repo)
    with_project = "\n".join(planners_mod.onboarding_lines(planner, repo))
    assert "configs/project.yaml" in with_project


def test_no_paste_block_when_the_planner_is_spawned(repo: Path, capsys) -> None:
    """A configured tier is briefed by the harness; a paste block would be noise."""
    from harness.cli import main
    from harness.init import init_project
    from harness.setup import build_config, load_platforms, write_agent_config

    init_project(repo, name="p")
    platforms = load_platforms(root=repo)
    tier = build_config(platforms["opencode"], model="deepseek/deepseek-v4-pro", effort="high")
    write_agent_config(tier, tier, root=repo)

    assert main(["create", "-n", "spawned", "--root", str(repo)]) == 0
    assert "paste this" not in capsys.readouterr().out
