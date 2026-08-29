"""First-run configuration: which platform, model, and reasoning level.

Tiering only means something if you can actually choose the tier. A Worker
(Tier 3) doing bounded, well-specified work should run on a small fast model;
reserving the expensive one for planning is the whole economic argument. So the
platform, the model, and the reasoning level are explicit settings, and a
command exists to set them — nothing is inherited from a tool's default, where
it would be invisible.

Platform knowledge lives in ``configs/worker-platforms.yaml`` as data. This
module reads that file; it hardcodes no vendor, no flag, and no model name.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

from harness.worker import DEFAULT_ATTEMPTS, WorkerConfig

DEFAULT_PLATFORMS_PATH = "configs/worker-platforms.yaml"
DEFAULT_WORKER_PATH = "configs/worker.yaml"


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
    docs: str = ""
    reads_brief_from: str = "stdin"

    @property
    def is_custom(self) -> bool:
        return not self.command.strip()


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
            docs=str(data.get("docs", "")),
            reads_brief_from=str(data.get("reads_brief_from", "stdin")),
        )
    return platforms


def build_config(
    platform: Platform,
    model: str,
    effort: str,
    attempts: int = DEFAULT_ATTEMPTS,
    command: str | None = None,
    label: str = "worker",
) -> WorkerConfig:
    """Turn a choice into a runnable worker configuration."""
    resolved = command if command is not None else platform.command
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
    return WorkerConfig(
        adapter="cli",
        platform=platform.name,
        model=model,
        effort=effort,
        command=resolved,
        resume_command=platform.resume_command if command is None else "",
        attempts=attempts,
        label=label,
    )


def config_to_dict(config: WorkerConfig) -> dict[str, Any]:
    data: dict[str, Any] = {
        "adapter": config.adapter,
        "platform": config.platform,
        "model": config.model,
        "effort": config.effort,
        "command": config.command,
    }
    if config.resume_command:
        data["resume_command"] = config.resume_command
    data["attempts"] = config.attempts
    data["timeout"] = config.timeout
    data["label"] = config.label
    return {"worker": data}


HEADER = """\
# Written by `harness setup`. Edit freely — it is ordinary configuration.
#
# Tier 3 (Workers) runs here. Their work is bounded and specified, so a small
# fast model is usually the right call; the expensive one belongs in planning.
# Re-run `harness setup` to change platform, model, or reasoning level.
#
# Placeholders: {model} {effort} {root} {brief_file} {task_file} {task_id}
"""


def write_worker_config(
    config: WorkerConfig, path: str | Path | None = None, root: str | Path = "."
) -> Path:
    target = Path(path) if path else Path(root) / DEFAULT_WORKER_PATH
    if not target.is_absolute():
        target = Path(root) / target
    target.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        config_to_dict(config), sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    target.write_text(HEADER + body, encoding="utf-8")
    return target
