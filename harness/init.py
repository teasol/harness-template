"""Project initialization and scaffolding for Research Harness.

Initializes a research repository with standard two-tier agent orchestration
contracts, platform configurations, and smoke-test verification specs,
neatly encapsulated under `.harness/` to avoid collisions with existing project files.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from harness.paths import (
    get_agents_config_path,
    get_harness_dir,
)

TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"


class InitError(Exception):
    """Raised when project initialization fails."""


def slugify(name: str) -> str:
    """Convert arbitrary project name into a clean kebab-case slug."""
    slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise InitError("Project name produces an empty slug")
    return slug


def init_project(
    target_dir: str | Path = ".",
    name: str | None = None,
    force: bool = False,
) -> list[Path]:
    """Scaffold a directory with harness templates and structure under .harness/."""
    target = Path(target_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)

    if not TEMPLATE_ROOT.is_dir():
        raise InitError(f"Harness template directory not found at: {TEMPLATE_ROOT}")

    # Safety check: avoid accidentally overwriting unless --force is given
    agents_yaml = get_agents_config_path(target)
    if agents_yaml.is_file() and not force:
        raise InitError(
            f"Project already initialized at {target} ('{agents_yaml.name}' exists). "
            "Use --force to overwrite existing files."
        )

    # Standard encapsulated directory skeleton
    harness_dir = get_harness_dir(target)
    dirs = [
        harness_dir / "plans",
        harness_dir / "tasks",
        harness_dir / "configs",
        harness_dir / "agents",
        harness_dir / "scripts",
        target / ".experiments",
        target / "results",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    created_files: list[Path] = []

    # Copy files from templates into .harness/ namespace
    files_to_copy = [
        ("AGENTS.md", target / "AGENTS.md"),
        ("agents/planner.md", harness_dir / "agents" / "planner.md"),
        ("agents/worker.md", harness_dir / "agents" / "worker.md"),
        ("configs/agent-platforms.yaml", harness_dir / "configs" / "agent-platforms.yaml"),
        ("configs/agents.yaml", harness_dir / "configs" / "agents.yaml"),
        ("configs/demo.yaml", harness_dir / "configs" / "demo.yaml"),
        # The demo spec runs this. Shipping the spec without it made the first
        # command in the quickstart — "prove it works here" — fail on every
        # fresh project, which is the worst possible first impression.
        ("scripts/demo_step.py", harness_dir / "scripts" / "demo_step.py"),
    ]

    for src_rel, dst_path in files_to_copy:
        src_path = TEMPLATE_ROOT / src_rel
        if not src_path.is_file():
            continue
        if dst_path.exists() and not force:
            continue
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src_path, dst_path)
        created_files.append(dst_path)

    # Handle .gitignore
    gitignore_src = TEMPLATE_ROOT / "gitignore"
    gitignore_dst = target / ".gitignore"
    if gitignore_src.is_file():
        entries_to_add = gitignore_src.read_text(encoding="utf-8")
        if not gitignore_dst.exists():
            gitignore_dst.write_text(entries_to_add, encoding="utf-8")
            created_files.append(gitignore_dst)
        else:
            existing = gitignore_dst.read_text(encoding="utf-8")
            missing_entries = []
            for line in entries_to_add.splitlines():
                if line and not line.startswith("#") and line not in existing:
                    missing_entries.append(line)
            if missing_entries:
                with gitignore_dst.open("a", encoding="utf-8") as f:
                    f.write("\n# Added by Research Harness\n")
                    for entry in missing_entries:
                        f.write(f"{entry}\n")

    return created_files
