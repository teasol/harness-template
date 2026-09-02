"""Centralized path resolution for Research Harness.

Supports both encapsulated layout (under `.harness/`) and legacy top-level layout
for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path


def has_harness_dir(root: str | Path = ".") -> bool:
    """Return True if the target directory contains a .harness directory."""
    return (Path(root) / ".harness").is_dir()


def get_harness_dir(root: str | Path = ".") -> Path:
    """Return the .harness directory inside root."""
    return Path(root) / ".harness"


def get_configs_dir(root: str | Path = ".") -> Path:
    """Return the configs directory (.harness/configs or configs/)."""
    r = Path(root)
    harness_configs = r / ".harness" / "configs"
    if harness_configs.is_dir():
        return harness_configs
    root_configs = r / "configs"
    if root_configs.is_dir():
        return root_configs
    # Default to .harness/configs if .harness exists, else configs
    if (r / ".harness").is_dir():
        return harness_configs
    return root_configs


def get_agents_config_path(root: str | Path = ".") -> Path:
    """Return path to agents.yaml (.harness/configs/agents.yaml or configs/agents.yaml)."""
    r = Path(root)
    harness_path = r / ".harness" / "configs" / "agents.yaml"
    if harness_path.is_file():
        return harness_path
    root_path = r / "configs" / "agents.yaml"
    if root_path.is_file():
        return root_path
    if (r / ".harness").is_dir():
        return harness_path
    return root_path


def get_platforms_config_path(root: str | Path = ".") -> Path:
    """Return path to agent-platforms.yaml."""
    r = Path(root)
    harness_path = r / ".harness" / "configs" / "agent-platforms.yaml"
    if harness_path.is_file():
        return harness_path
    root_path = r / "configs" / "agent-platforms.yaml"
    if root_path.is_file():
        return root_path
    if (r / ".harness").is_dir():
        return harness_path
    return root_path


def get_plans_dir(root: str | Path = ".") -> Path:
    """Return plans directory (.harness/plans or plans/)."""
    r = Path(root)
    harness_plans = r / ".harness" / "plans"
    if harness_plans.is_dir():
        return harness_plans
    root_plans = r / "plans"
    if root_plans.is_dir():
        return root_plans
    if (r / ".harness").is_dir():
        return harness_plans
    return root_plans


def get_tasks_dir(root: str | Path = ".") -> Path:
    """Return tasks directory (.harness/tasks or tasks/)."""
    r = Path(root)
    harness_tasks = r / ".harness" / "tasks"
    if harness_tasks.is_dir():
        return harness_tasks
    root_tasks = r / "tasks"
    if root_tasks.is_dir():
        return root_tasks
    if (r / ".harness").is_dir():
        return harness_tasks
    return root_tasks


def get_agents_dir(root: str | Path = ".") -> Path:
    """Return agents contract directory (.harness/agents or agents/)."""
    r = Path(root)
    harness_agents = r / ".harness" / "agents"
    if harness_agents.is_dir():
        return harness_agents
    root_agents = r / "agents"
    if root_agents.is_dir():
        return root_agents
    if (r / ".harness").is_dir():
        return harness_agents
    return root_agents


def template_root() -> Path:
    """Where the files `harness init` copies out actually live.

    They are not part of the code, so they do not sit in the code's directory:
    in this repository they are the top-level `templates/`. Installed, they
    arrive as the `harness_templates` distribution package beside `harness`,
    because a wheel can only carry data that belongs to some package — a bare
    top-level directory is not shipped at all. Both layouts are probed here so
    that nothing else has to know which one it is looking at.
    """
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parent / "templates",  # this checkout
        here.parent / "harness_templates",  # installed beside the package
        here / "templates",  # the pre-0.8 layout, inside the package
    ):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"harness templates not found: looked for templates/ and harness_templates/ next to {here}"
    )
