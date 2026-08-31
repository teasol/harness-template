"""The prefix printed commands carry.

A next step the user cannot paste is worse than none: it looks like an
instruction and fails. `uv add`-ing the harness is the case that used to break —
neither `harness` nor `python -m harness` resolves outside the venv there.
"""

from __future__ import annotations

from pathlib import Path

from harness import invocation


def test_module_invocation_keeps_python_m() -> None:
    assert invocation.command_prefix({}, ["/repo/harness/__main__.py"]) == "python -m harness"


def test_installed_console_script_is_the_bare_name() -> None:
    assert invocation.command_prefix({}, ["/usr/local/bin/harness"]) == "harness"


def test_uv_run_is_detected_from_the_environment() -> None:
    env = {
        "UV": "/home/u/.local/bin/uv",
        "UV_RUN_RECURSION_DEPTH": "1",
        "VIRTUAL_ENV": "/proj/.venv",
    }
    assert invocation.command_prefix(env, ["/proj/.venv/bin/harness"]) == "uv run harness"


def test_uv_tool_install_is_not_uv_run() -> None:
    """`uv tool install` puts `harness` on the PATH; the bare name is right."""
    assert (
        invocation.command_prefix({"UV": "/home/u/.local/bin/uv"}, ["/home/u/.local/bin/harness"])
        == "harness"
    )


def test_steps_align_comments_under_the_prefix(monkeypatch) -> None:
    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    rendered = invocation.steps([("create -n <planner-name>", "first"), ("plans", "later")])
    assert rendered[0].startswith("uv run harness create -n <planner-name>")
    # One column for both comments, computed rather than typed.
    assert len({line.index("#") for line in rendered}) == 1


def test_init_output_names_a_runnable_command(tmp_path: Path, monkeypatch, capsys) -> None:
    from harness.cli import main

    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    monkeypatch.setenv("UV", "/home/u/.local/bin/uv")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv"))
    assert main(["init", str(tmp_path), "--no-setup"]) == 0
    out = capsys.readouterr().out
    assert "uv run harness create -n <planner-name>" in out
    # Not a command this user has.
    assert "\n  1. harness create" not in out


def test_scaffolded_contracts_are_runnable_too(tmp_path: Path, monkeypatch) -> None:
    """The Planner reads AGENTS.md and runs what it says."""
    from harness.init import init_project

    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    init_project(tmp_path, name="p")
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "uv run harness status" in agents
    assert "python -m harness" not in agents


def test_create_briefs_the_planner_with_the_same_prefix(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from harness.cli import main
    from harness.init import init_project

    init_project(tmp_path, name="p")
    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    assert main(["create", "-n", "first-planner", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "uv run harness plan new <name> --planner first-planner" in out
    assert "uv run harness planner set first-planner" in out
    assert "uv run harness status" in out
