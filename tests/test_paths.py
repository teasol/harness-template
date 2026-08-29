"""Tests for centralized path resolution (harness.paths)."""

from __future__ import annotations

from pathlib import Path

from harness.paths import (
    get_agents_config_path,
    get_agents_dir,
    get_configs_dir,
    get_harness_dir,
    get_plans_dir,
    get_platforms_config_path,
    get_tasks_dir,
    has_harness_dir,
)


def test_paths_with_encapsulated_harness(tmp_path: Path) -> None:
    harness_dir = tmp_path / ".harness"
    (harness_dir / "configs").mkdir(parents=True)
    (harness_dir / "plans").mkdir(parents=True)
    (harness_dir / "tasks").mkdir(parents=True)
    (harness_dir / "agents").mkdir(parents=True)

    (harness_dir / "configs" / "agents.yaml").write_text("dummy", encoding="utf-8")
    (harness_dir / "configs" / "agent-platforms.yaml").write_text("dummy", encoding="utf-8")

    assert has_harness_dir(tmp_path) is True
    assert get_harness_dir(tmp_path) == harness_dir
    assert get_configs_dir(tmp_path) == harness_dir / "configs"
    assert get_plans_dir(tmp_path) == harness_dir / "plans"
    assert get_tasks_dir(tmp_path) == harness_dir / "tasks"
    assert get_agents_dir(tmp_path) == harness_dir / "agents"
    assert get_agents_config_path(tmp_path) == harness_dir / "configs" / "agents.yaml"
    assert get_platforms_config_path(tmp_path) == harness_dir / "configs" / "agent-platforms.yaml"


def test_paths_fallback_to_root(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "plans").mkdir()
    (tmp_path / "tasks").mkdir()
    (tmp_path / "agents").mkdir()

    (tmp_path / "configs" / "agents.yaml").write_text("dummy", encoding="utf-8")
    (tmp_path / "configs" / "agent-platforms.yaml").write_text("dummy", encoding="utf-8")

    assert has_harness_dir(tmp_path) is False
    assert get_configs_dir(tmp_path) == tmp_path / "configs"
    assert get_plans_dir(tmp_path) == tmp_path / "plans"
    assert get_tasks_dir(tmp_path) == tmp_path / "tasks"
    assert get_agents_dir(tmp_path) == tmp_path / "agents"
    assert get_agents_config_path(tmp_path) == tmp_path / "configs" / "agents.yaml"
    assert get_platforms_config_path(tmp_path) == tmp_path / "configs" / "agent-platforms.yaml"
