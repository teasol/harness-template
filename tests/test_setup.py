"""Tests for first-run configuration.

Tiering is only real if the tier can be chosen. These cover the choosing:
platform presets are data, the values reach the command, and a nonsense
choice is refused rather than silently falling back to a platform default.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.setup import (
    SetupError,
    build_config,
    config_to_dict,
    load_platforms,
    write_agent_config,
)
from harness.worker import AgentConfig, WorkerError, load_agent_config, load_worker_config


def test_shipped_presets_load() -> None:
    platforms = load_platforms(root=".")
    assert {"manual", "claude", "codex", "opencode", "antigravity", "custom"} <= set(platforms)
    for name, platform in platforms.items():
        if name in ("custom", "manual"):
            continue
        # Every real preset must let the tier be chosen, or it defeats the point.
        assert "{model}" in platform.command, f"{name} cannot select a model"
        assert "{effort}" in platform.command, f"{name} cannot select a reasoning level"
        assert platform.efforts, f"{name} lists no reasoning levels"
        assert platform.docs, f"{name} should say how to check its flags"


def test_manual_platform_builds_manual_adapter() -> None:
    platform = load_platforms(root=".")["manual"]
    planner = build_config(platform, attempts=3, label="planner")
    worker = build_config(platform, attempts=6, label="worker")
    assert planner.adapter == "manual"
    assert worker.adapter == "manual"
    assert config_to_dict(planner) == {"adapter": "manual", "attempts": 3, "label": "planner"}
    assert config_to_dict(worker) == {"adapter": "manual", "attempts": 6, "label": "worker"}


def test_missing_presets_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SetupError, match="not found"):
        load_platforms(root=tmp_path)


def test_build_config_injects_the_tier() -> None:
    platform = load_platforms(root=".")["claude"]
    config = build_config(platform, model="haiku", effort="low")
    assert config.adapter == "cli"
    assert config.model == "haiku" and config.effort == "low"
    assert config.platform == "claude"


def test_unknown_reasoning_level_is_refused() -> None:
    platform = load_platforms(root=".")["claude"]
    with pytest.raises(SetupError, match="not a reasoning level"):
        build_config(platform, model="haiku", effort="turbo")


def test_model_is_required_when_the_command_wants_one() -> None:
    platform = load_platforms(root=".")["claude"]
    with pytest.raises(SetupError, match="needs a model"):
        build_config(platform, model="", effort="low")


def test_custom_platform_needs_its_own_command() -> None:
    platform = load_platforms(root=".")["custom"]
    with pytest.raises(SetupError, match="no command preset"):
        build_config(platform, model="x", effort="")
    config = build_config(platform, model="x", effort="", command="run-my-agent {root}")
    assert config.command == "run-my-agent {root}"


def test_both_tiers_round_trip(tmp_path: Path) -> None:
    """Both tiers are written to one file, and each loads back independently."""
    platform = load_platforms(root=".")["claude"]
    planner = build_config(platform, model="opus", effort="high", attempts=3, label="planner")
    worker = build_config(platform, model="haiku", effort="low", attempts=6)
    path = write_agent_config(planner, worker, root=tmp_path)

    assert path == tmp_path / "configs" / "agents.yaml"
    loaded_planner = load_agent_config("planner", root=tmp_path)
    loaded_worker = load_agent_config("worker", root=tmp_path)
    assert (loaded_planner.model, loaded_planner.effort) == ("opus", "high")
    assert (loaded_worker.model, loaded_worker.effort) == ("haiku", "low")
    assert loaded_planner.attempts == 3 and loaded_worker.attempts == 6
    assert load_worker_config(root=tmp_path).model == "haiku"
    # Readable as plain YAML, since it is ordinary configuration.
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["planner"]["model"] == "opus" and raw["worker"]["model"] == "haiku"
    assert config_to_dict(worker)["effort"] == "low"


def test_planner_defaults_to_the_reasoning_tier() -> None:
    """The preset's planner defaults should not be the worker's small model."""
    platform = load_platforms(root=".")["claude"]
    assert platform.planner_model and platform.planner_model != platform.default_model
    assert platform.planner_effort != platform.default_effort


def test_attaching_a_session_switches_the_command(tmp_path: Path) -> None:
    """A researcher with a session already open can hand it to a tier."""
    platform = load_platforms(root=".")["claude"]
    config = build_config(platform, model="opus", effort="high", label="planner", session="abc-123")
    assert config.session == "abc-123"
    assert "{session}" in config.command
    assert "--resume" in config.command


def test_a_session_command_without_a_session_is_refused() -> None:
    platform = load_platforms(root=".")["claude"]
    with pytest.raises(SetupError, match="none was given"):
        build_config(
            platform,
            model="opus",
            effort="high",
            command=platform.session_command,
            session="",
        )


def test_a_command_wanting_a_model_without_one_is_refused() -> None:
    """Otherwise the Worker quietly runs at the platform default."""
    with pytest.raises(WorkerError, match="no 'model' is set"):
        AgentConfig.from_dict({"adapter": "cli", "command": "agent --model {model}"})
    with pytest.raises(WorkerError, match="no 'effort' is set"):
        AgentConfig.from_dict({"adapter": "cli", "command": "agent --effort {effort}"})


def test_presets_let_an_agent_actually_run_things() -> None:
    """Unattended agents must be able to execute, not only edit.

    The first live run produced a correct plan and then stopped to ask for
    permission to run `plan materialize`, because the preset only accepted
    edits. A preset that cannot execute is a preset that cannot finish.
    """
    platforms = load_platforms(root=".")
    assert "acceptEdits" not in platforms["claude"].command
    assert "bypassPermissions" in platforms["claude"].command
    assert "bypassPermissions" in platforms["claude"].resume_command
    assert "dangerously-skip-permissions" in platforms["antigravity"].command
    assert "dangerously-skip-permissions" in platforms["antigravity"].resume_command
