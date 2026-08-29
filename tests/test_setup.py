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
    write_worker_config,
)
from harness.worker import WorkerConfig, WorkerError, load_worker_config


def test_shipped_presets_load() -> None:
    platforms = load_platforms(root=".")
    assert {"claude", "codex", "opencode", "custom"} <= set(platforms)
    for name, platform in platforms.items():
        if name == "custom":
            assert platform.is_custom
            continue
        # Every real preset must let the tier be chosen, or it defeats the point.
        assert "{model}" in platform.command, f"{name} cannot select a model"
        assert "{effort}" in platform.command, f"{name} cannot select a reasoning level"
        assert platform.efforts, f"{name} lists no reasoning levels"
        assert platform.docs, f"{name} should say how to check its flags"


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


def test_written_config_round_trips(tmp_path: Path) -> None:
    platform = load_platforms(root=".")["claude"]
    config = build_config(platform, model="haiku", effort="low", attempts=3)
    path = write_worker_config(config, root=tmp_path)

    assert path == tmp_path / "configs" / "worker.yaml"
    reloaded = load_worker_config(root=tmp_path)
    assert reloaded.adapter == "cli"
    assert reloaded.platform == "claude"
    assert reloaded.model == "haiku"
    assert reloaded.effort == "low"
    assert reloaded.attempts == 3
    # Readable as plain YAML, since it is ordinary configuration.
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["worker"]["model"] == "haiku"
    assert config_to_dict(config)["worker"]["effort"] == "low"


def test_a_command_wanting_a_model_without_one_is_refused() -> None:
    """Otherwise the Worker quietly runs at the platform default."""
    with pytest.raises(WorkerError, match="no 'model' is set"):
        WorkerConfig.from_dict({"adapter": "cli", "command": "agent --model {model}"})
    with pytest.raises(WorkerError, match="no 'effort' is set"):
        WorkerConfig.from_dict({"adapter": "cli", "command": "agent --effort {effort}"})
