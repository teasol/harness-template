"""Tests for project initialization (harness init)."""

from __future__ import annotations

from pathlib import Path
import pytest

from harness.cli import find_project_root, main
from harness.init import InitError, init_project


def test_init_fresh_directory(tmp_path: Path) -> None:
    project_dir = tmp_path / "my_project"
    created = init_project(project_dir)

    assert len(created) >= 7
    assert (project_dir / "AGENTS.md").is_file()
    assert (project_dir / "agents" / "planner.md").is_file()
    assert (project_dir / "agents" / "worker.md").is_file()
    assert (project_dir / "configs" / "agents.yaml").is_file()
    assert (project_dir / "configs" / "agent-platforms.yaml").is_file()
    assert (project_dir / "configs" / "demo.yaml").is_file()
    assert (project_dir / "scripts" / "demo_step.py").is_file()
    assert (project_dir / ".gitignore").is_file()

    gitignore_text = (project_dir / ".gitignore").read_text(encoding="utf-8")
    assert ".experiments/" in gitignore_text
    assert "results/*" in gitignore_text

    assert (project_dir / "plans").is_dir()
    assert (project_dir / "tasks").is_dir()
    assert (project_dir / ".experiments").is_dir()
    assert (project_dir / "results").is_dir()


def test_init_existing_directory_without_force_raises(tmp_path: Path) -> None:
    init_project(tmp_path)
    with pytest.raises(InitError, match="already initialized"):
        init_project(tmp_path, force=False)


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
    assert (target / "configs" / "agents.yaml").is_file()


def test_find_project_root(tmp_path: Path) -> None:
    project_dir = tmp_path / "root_proj"
    init_project(project_dir)

    nested = project_dir / "src" / "subpackage" / "module"
    nested.mkdir(parents=True)

    found = find_project_root(nested)
    assert found == project_dir.resolve()
