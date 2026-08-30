"""Tests for project initialization (harness init)."""

from __future__ import annotations

import os
from pathlib import Path

from harness.cli import find_project_root, main
from harness.init import init_project


def test_init_fresh_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "my_project"
    created = init_project(project_dir)

    assert len(created) >= 6
    assert (project_dir / "AGENTS.md").is_file()
    assert (project_dir / ".harness" / "agents" / "planner.md").is_file()
    assert (project_dir / ".harness" / "agents" / "worker.md").is_file()
    assert (project_dir / ".harness" / "configs" / "agents.yaml").is_file()
    assert (project_dir / ".harness" / "configs" / "agent-platforms.yaml").is_file()
    assert (project_dir / ".harness" / "configs" / "demo.yaml").is_file()
    assert (project_dir / ".gitignore").is_file()

    gitignore_text = (project_dir / ".gitignore").read_text(encoding="utf-8")
    assert ".worktrees/" in gitignore_text
    assert "results/*" in gitignore_text

    assert (project_dir / ".harness" / "plans").is_dir()
    assert (project_dir / ".harness" / "tasks").is_dir()
    assert (project_dir / ".worktrees").is_dir()
    assert (project_dir / "results").is_dir()


def test_init_preserves_existing_project_configs_and_tasks(tmp_path: Path) -> None:
    """Initializing an existing project with configs/ and tasks/ must never collide."""
    project_dir = tmp_path / "existing_ml_project"
    project_dir.mkdir()

    # Simulate existing project structure (Hydra configs, tasks data, training script)
    (project_dir / "configs").mkdir()
    (project_dir / "configs" / "model.yaml").write_text("model: resnet50\n", encoding="utf-8")
    (project_dir / "tasks").mkdir()
    (project_dir / "tasks" / "bench.py").write_text("print('eval')\n", encoding="utf-8")
    (project_dir / "scripts").mkdir()
    (project_dir / "scripts" / "train.sh").write_text(
        "#!/bin/bash\npython train.py\n", encoding="utf-8"
    )

    # Run harness init
    init_project(project_dir)

    # Existing project files are 100% untouched
    cfg_text = (project_dir / "configs" / "model.yaml").read_text(encoding="utf-8")
    assert cfg_text == "model: resnet50\n"
    assert (project_dir / "tasks" / "bench.py").is_file()
    assert (project_dir / "scripts" / "train.sh").is_file()
    assert not (project_dir / "configs" / "agents.yaml").exists()  # Kept inside .harness!

    # Harness files are cleanly encapsulated in .harness/
    assert (project_dir / ".harness" / "configs" / "agents.yaml").is_file()
    assert (project_dir / ".harness" / "plans").is_dir()
    assert (project_dir / ".harness" / "tasks").is_dir()


def test_rerunning_init_adds_what_is_missing_and_keeps_the_rest(tmp_path: Path) -> None:
    """A project set up by an older version must be able to catch up.

    It used to refuse, and `--force` was the only way forward — which overwrites
    `agents.yaml` too, throwing away the platform, model and command a lab had
    configured in order to install a file that was merely missing.
    """
    init_project(tmp_path)
    agents = tmp_path / ".harness" / "configs" / "agents.yaml"
    agents.write_text("worker:\n  adapter: cli\n  command: my-own-agent\n", encoding="utf-8")
    missing = tmp_path / ".harness" / "scripts" / "demo_step.py"
    missing.unlink()

    result = init_project(tmp_path)

    assert result.already_initialized
    assert missing.is_file(), "the absent file is restored"
    assert [p.name for p in result.created] == ["demo_step.py"]
    assert agents in result.kept
    assert "my-own-agent" in agents.read_text(encoding="utf-8"), "config must survive"


def test_rerunning_init_when_nothing_is_missing_changes_nothing(tmp_path: Path) -> None:
    init_project(tmp_path)
    result = init_project(tmp_path)
    assert result.already_initialized
    assert result.created == []
    assert result.kept


def test_rerun_reports_what_it_did(tmp_path: Path, capsys) -> None:
    init_project(tmp_path)
    (tmp_path / ".harness" / "scripts" / "demo_step.py").unlink()
    assert main(["init", str(tmp_path), "--no-setup"]) == 0
    out = capsys.readouterr().out
    assert "Updated Research Harness" in out
    assert "left untouched" in out
    assert "agent configuration" in out


def test_init_with_force_overwrites(tmp_path: Path) -> None:
    init_project(tmp_path)
    # Modify a file
    (tmp_path / "AGENTS.md").write_text("modified", encoding="utf-8")
    init_project(tmp_path, force=True)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") != "modified"


def test_cli_init_command(tmp_path: Path, capsys) -> None:
    target = tmp_path / "cli_project"
    code = main(["init", str(target), "--no-setup"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Initialized Research Harness" in out
    assert (target / ".harness" / "configs" / "agents.yaml").is_file()


def test_find_project_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "root_proj"
    init_project(project_dir)

    nested = project_dir / "src" / "subpackage" / "module"
    nested.mkdir(parents=True)

    found = find_project_root(nested)
    assert found == project_dir.resolve()


def test_the_quickstart_actually_runs_on_a_fresh_project(tmp_path: Path) -> None:
    """`harness init` then `harness verify --spec configs/demo.yaml`, verbatim.

    Two things had quietly broken this, and both are exactly what a first
    impression is made of: `init` writes specs under `.harness/` so they cannot
    collide with a project's own `configs/`, but `--spec configs/demo.yaml` did
    not look there; and the demo spec was shipped without the script it runs.
    """
    from harness.cli import main

    assert main(["init", str(tmp_path), "--no-setup"]) == 0
    # The script the demo spec runs must actually be installed.
    assert (tmp_path / ".harness" / "scripts" / "demo_step.py").is_file()

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert main(["verify", "--spec", "configs/demo.yaml"]) == 0
    finally:
        os.chdir(cwd)


def test_spec_path_falls_back_to_the_harness_dir(tmp_path: Path) -> None:
    from harness.cli import _resolve_spec_path

    (tmp_path / ".harness" / "configs").mkdir(parents=True)
    (tmp_path / ".harness" / "configs" / "x.yaml").write_text("name: x\n", encoding="utf-8")
    resolved = _resolve_spec_path("configs/x.yaml", tmp_path)
    assert resolved.endswith(".harness/configs/x.yaml")
    # An explicit path that exists is never rewritten.
    assert _resolve_spec_path("configs/missing.yaml", tmp_path) == "configs/missing.yaml"
