"""First-run configuration: which platform, model, and reasoning level.

Tiering only means something if you can actually choose the tier. A Worker
(Tier 3) doing bounded, well-specified work should run on a small fast model;
reserving the expensive one for planning is the whole economic argument. So the
platform, the model, and the reasoning level are explicit settings, and a
command exists to set them — nothing is inherited from a tool's default, where
it would be invisible.

Platform knowledge lives in ``configs/agent-platforms.yaml`` as data. This
module reads that file; it hardcodes no vendor, no flag, and no model name.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

from harness.worker import DEFAULT_ATTEMPTS, AgentConfig

DEFAULT_PLATFORMS_PATH = "configs/agent-platforms.yaml"
DEFAULT_AGENTS_PATH = "configs/agents.yaml"


class SetupError(ValueError):
    """Raised when configuration cannot be read or a choice is not valid."""


@dataclasses.dataclass
class Platform:
    """One entry from the presets file."""

    name: str
    label: str
    command: str = ""
    resume_command: str = ""
    efforts: list[str] = dataclasses.field(default_factory=list)
    default_effort: str = ""
    model_hint: str = ""
    default_model: str = ""
    planner_model: str = ""
    planner_effort: str = ""
    session_command: str = ""
    docs: str = ""
    reads_brief_from: str = "stdin"

    @property
    def is_manual(self) -> bool:
        return self.name == "manual"

    @property
    def is_custom(self) -> bool:
        return self.name != "manual" and not self.command.strip()


def load_platforms(path: str | Path | None = None, root: str | Path = ".") -> dict[str, Platform]:
    """Read the platform presets. Absent file is an error, not a silent default."""
    candidate = Path(path) if path else Path(root) / DEFAULT_PLATFORMS_PATH
    if not candidate.is_absolute():
        candidate = Path(root) / candidate
    if not candidate.is_file():
        raise SetupError(f"platform presets not found: {candidate}")
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SetupError(f"invalid YAML in {candidate}: {exc}") from exc
    entries = raw.get("platforms")
    if not isinstance(entries, dict) or not entries:
        raise SetupError(f"{candidate} has no 'platforms' mapping")

    platforms: dict[str, Platform] = {}
    for name, data in entries.items():
        if not isinstance(data, dict):
            raise SetupError(f"platform '{name}' must be a mapping")
        platforms[str(name)] = Platform(
            name=str(name),
            label=str(data.get("label", name)),
            command=str(data.get("command", "")).strip(),
            resume_command=str(data.get("resume_command", "")).strip(),
            efforts=[str(e) for e in data.get("efforts", [])],
            default_effort=str(data.get("default_effort", "")),
            model_hint=str(data.get("model_hint", "")),
            default_model=str(data.get("default_model", "")),
            planner_model=str(data.get("planner_model", "")),
            planner_effort=str(data.get("planner_effort", "")),
            session_command=str(data.get("session_command", "")).strip(),
            docs=str(data.get("docs", "")),
            reads_brief_from=str(data.get("reads_brief_from", "stdin")),
        )
    return platforms


def build_config(
    platform: Platform,
    model: str = "",
    effort: str = "",
    attempts: int = DEFAULT_ATTEMPTS,
    command: str | None = None,
    label: str = "worker",
    session: str = "",
) -> AgentConfig:
    """Turn a choice into a runnable agent configuration for one tier."""
    if platform.is_manual:
        return AgentConfig(
            adapter="manual",
            platform="manual",
            attempts=attempts,
            label=label,
        )
    resolved = command if command is not None else platform.command
    if session and command is None and platform.session_command:
        # Attaching an existing session: the researcher already opened one and
        # wants this tier to continue in it rather than start fresh.
        resolved = platform.session_command
    if not resolved.strip():
        raise SetupError(
            f"platform '{platform.name}' has no command preset — pass one explicitly "
            "(it must take the briefing, edit files under {root}, and exit)."
        )
    if platform.efforts and effort and effort not in platform.efforts:
        raise SetupError(
            f"'{effort}' is not a reasoning level {platform.label} accepts. "
            f"choose from: {', '.join(platform.efforts)}"
        )
    if "{model}" in resolved and not model:
        raise SetupError(f"{platform.label} needs a model ({platform.model_hint})")
    if "{session}" in resolved and not session:
        raise SetupError(
            f"{platform.label} was asked to attach a session but none was given "
            "(pass --planner-session / --worker-session)"
        )
    return AgentConfig(
        adapter="cli",
        platform=platform.name,
        model=model,
        effort=effort,
        command=resolved,
        resume_command=platform.resume_command if command is None and not session else "",
        attempts=attempts,
        label=label,
        session=session,
    )


def config_to_dict(config: AgentConfig) -> dict[str, Any]:
    if config.adapter == "manual":
        return {
            "adapter": "manual",
            "attempts": config.attempts,
            "label": config.label,
        }
    data: dict[str, Any] = {
        "adapter": config.adapter,
        "platform": config.platform,
        "model": config.model,
        "effort": config.effort,
        "command": config.command,
    }
    if config.resume_command:
        data["resume_command"] = config.resume_command
    if config.session:
        data["session"] = config.session
    data["attempts"] = config.attempts
    data["timeout"] = config.timeout
    data["label"] = config.label
    return data


HEADER = """\
# Written by `harness setup`. Edit freely — it is ordinary configuration.
#
# Two tiers, side by side so the split is visible:
#
#   planner — Tier 2. Decomposes the goal, judges the whole. The reasoning
#             happens here, so this is where an expensive model earns its cost.
#   worker  — Tier 3. Builds one fully-specified module against machine-checked
#             acceptance. Bounded work; a small fast model usually suffices.
#
# `adapter: manual` means you open that session yourself; `cli` means the
# harness spawns it. Set `session:` to attach an already-open session instead
# of starting a fresh one.
#
# Re-run `harness setup` to change any of it.
# Placeholders: {model} {effort} {session} {root} {brief_file}
#               {task_file} {task_id} {experiment}
"""


def write_agent_config(
    planner: AgentConfig,
    worker: AgentConfig,
    path: str | Path | None = None,
    root: str | Path = ".",
) -> Path:
    """Write both tiers into one file."""
    target = Path(path) if path else Path(root) / DEFAULT_AGENTS_PATH
    if not target.is_absolute():
        target = Path(root) / target
    target.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"planner": config_to_dict(planner), "worker": config_to_dict(worker)},
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    target.write_text(HEADER + body, encoding="utf-8")
    return target
