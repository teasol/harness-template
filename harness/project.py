"""Project context: what a Planner must know before it plans anything.

A harness dropped into an existing project inherits a world it did not build —
a numbers-of-record document, a house reporting format, a script that resolves
per-machine paths, conventions about which approaches are already closed. Today
a Planner discovers all of that by reading around, every time, and a Planner
that reads the wrong document plans against the wrong facts.

That happened on the first real run: a summary line in a status document read
``SMAD4 0.4282 -> 0.5483``, which is a *branch* figure, while the authoritative
per-task table two hundred lines further down said the arm scored ``0.4465``.
The two are eleven points apart and the Planner nearly reported a dead branch.
Nothing was wrong with the documents; nothing told the Planner which of them
was the source of truth.

So the project says it once, here, and the harness puts it in front of every
Planner. This file holds no vendor knowledge and no research logic — it is a
project's own description of itself.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

from harness import invocation
from harness.paths import get_configs_dir

CONFIG_NAME = "project.yaml"


class ProjectError(ValueError):
    """Raised when the project context cannot be read."""


@dataclasses.dataclass
class ProjectContext:
    """A project's own account of how work in it must be done."""

    #: Named documents a Planner should read, e.g. {"authority": "docs/status.md"}.
    #: ``authority`` is special: it names the document that wins when two
    #: sources disagree about a number.
    docs: dict[str, str] = dataclasses.field(default_factory=dict)
    #: Where the house reporting format is defined, if the project has one.
    report_format: str = ""
    #: A script that resolves per-machine paths, interpreters, and devices.
    environment: str = ""
    #: The interpreter this project's own code runs under. Exported to steps as
    #: PROJECT_PYTHON. Distinct from HARNESS_PYTHON, which is whatever
    #: interpreter is running the harness and generally has no project deps.
    python: str = ""
    #: Hard rules that plans must respect — closed directions, forbidden
    #: statistics, anything a newcomer would otherwise have to be told twice.
    conventions: list[str] = dataclasses.field(default_factory=list)
    source: Path | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.docs or self.report_format or self.environment or self.conventions)

    @property
    def authority_doc(self) -> str:
        return self.docs.get("authority", "")


def config_path(root: str | Path = ".") -> Path:
    return get_configs_dir(root) / CONFIG_NAME


def load_project_context(root: str | Path = ".") -> ProjectContext:
    """Read the project context, or return an empty one if none is declared.

    Absent is not an error: a fresh template has no project to describe yet.
    """
    path = config_path(root)
    if not path.is_file():
        return ProjectContext()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ProjectError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProjectError(f"{path} must be a mapping")
    entry = raw.get("project", raw)
    if not isinstance(entry, dict):
        raise ProjectError(f"{path}: 'project' must be a mapping")

    docs = entry.get("docs", {}) or {}
    if not isinstance(docs, dict):
        raise ProjectError(f"{path}: 'project.docs' must be a mapping of name -> path")
    conventions = entry.get("conventions", []) or []
    if not isinstance(conventions, list):
        raise ProjectError(f"{path}: 'project.conventions' must be a list")

    return ProjectContext(
        docs={str(k): str(v) for k, v in docs.items()},
        report_format=str(entry.get("report_format", "") or ""),
        environment=str(entry.get("environment", "") or ""),
        python=str(entry.get("python", "") or ""),
        conventions=[str(c) for c in conventions],
        source=path,
    )


def missing_docs(context: ProjectContext, root: str | Path = ".") -> list[str]:
    """Declared documents that are not actually there.

    A context pointing at a moved file is worse than none: the Planner is told
    where the truth lives and finds nothing, so it falls back to guessing while
    believing it was briefed.
    """
    root = Path(root)
    missing = []
    for name, rel in sorted(context.docs.items()):
        if not (root / rel).exists():
            missing.append(f"{name}: {rel}")
    for label, rel in (
        ("report_format", context.report_format),
        ("environment", context.environment),
    ):
        if not rel:
            continue
        candidate = rel.split("#", 1)[0]
        if not (root / candidate).exists():
            missing.append(f"{label}: {rel}")
    return missing


TEMPLATE = """\
# What a Planner must know before it plans anything in this project.
#
# The harness puts this in front of every Planner, so it is written once here
# rather than rediscovered — differently, and sometimes wrongly — each time.
# Delete any key that does not apply; an empty file is valid.

project:
  docs:
    # `authority` is the document that WINS when two sources disagree about a
    # number. Point it at the detailed table, not a summary: summaries
    # compress, and a compressed number read as an authoritative one is how a
    # Planner reports a result nobody measured.
    authority: docs/current_status.md
    architecture: docs/current_architecture.md
    protocol: docs/current_experiments.md

  # Where this project's house reporting format is defined. Experiment reports
  # follow it instead of the harness's own shape.
  report_format: docs/current_status.md

  # A script that resolves per-machine paths, interpreters and devices.
  # Acceptance steps should source this rather than hardcoding a path.
  environment: scripts/node_env.sh

  # The interpreter this project's code runs under, exported to every step as
  # PROJECT_PYTHON. Note this is NOT ${HARNESS_PYTHON}, which is whatever
  # interpreter runs the harness and usually has none of your dependencies.
  python: ""

  # Hard rules a plan must respect. Write the ones a newcomer would otherwise
  # have to be told twice — closed directions, forbidden statistics, anything
  # already settled.
  conventions: []
"""


def write_template(root: str | Path = ".", force: bool = False) -> Path:
    """Scaffold a commented project.yaml for a human to fill in."""
    path = config_path(root)
    if path.exists() and not force:
        raise ProjectError(f"{path} already exists (use --force to overwrite)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE, encoding="utf-8")
    return path


def brief_lines(context: ProjectContext, root: str | Path = ".") -> list[str]:
    """Render the context as the section every Planner briefing opens with."""
    if context.is_empty:
        return [
            "## Project context",
            "",
            "None declared. If this project has documents, a reporting format or",
            "conventions a Planner must respect, register them once so the next",
            "Planner is told rather than left to infer:",
            "",
            "```bash",
            f"{invocation.cmd('project init')}   # then edit the file it writes",
            "```",
            "",
        ]

    lines = ["## Project context", ""]
    if context.authority_doc:
        lines += [
            f"- **Numbers of record: `{context.authority_doc}`** — when two sources",
            "  disagree, this one wins. Prefer its detailed tables over any summary",
            "  line; a summary can compress two different quantities into one.",
        ]
    for name, rel in sorted(context.docs.items()):
        if name == "authority":
            continue
        lines.append(f"- {name}: `{rel}`")
    if context.report_format:
        lines.append(f"- Report in this project's own format: `{context.report_format}`")
    if context.environment:
        lines.append(f"- Resolve paths and interpreters via `{context.environment}` — source it")
        lines.append("  in acceptance steps rather than hardcoding anything.")
    if context.python:
        lines.append(
            f"- Project interpreter: `{context.python}` (steps get it as `${{PROJECT_PYTHON}}`; "
            "`${HARNESS_PYTHON}` is the harness's own and has none of this project's deps)"
        )
    if context.conventions:
        lines += ["", "**Conventions this plan must respect:**", ""]
        lines += [f"{i}. {rule}" for i, rule in enumerate(context.conventions, 1)]

    gaps = missing_docs(context, root)
    if gaps:
        lines += [
            "",
            "> **Registered but missing** — these paths do not exist, so verify before",
            "> relying on them, and tell the researcher:",
            "",
        ]
        lines += [f"> - {gap}" for gap in gaps]
    lines.append("")
    return lines
