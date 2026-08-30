"""Arriving in a codebase that already exists.

Most harness projects do not start empty. The harness lands on top of years of
research code that already produces the numbers of record, and on day one none
of it is covered by a contract, an acceptance check, or a plan. That is a
different situation from a fresh project, and until now it looked identical:
``harness init`` printed the same next steps either way.

What this module does **not** do is prescribe how to modularize. Deciding what
the modules are is the Planner's job — that is the entire point of Tier 2, and
a fixed procedure baked into the tool would take it away. Every codebase joins
for a different reason and is shaped differently, so a pipeline that was right
for one would be wrong for the next.

What it does instead is make the situation *visible* and hand the Planner the
judgement aids that generalize:

- a record of how the harness arrived (which commit, on top of how much code),
  so "unverified" has a concrete boundary rather than being a feeling;
- the conditions a module boundary has to satisfy here, which follow from what
  the harness can actually enforce;
- one ordering principle that is not obvious and is expensive to learn the
  hard way.

The Planner reads those and plans. It may disagree with them.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from harness.paths import get_harness_dir

MARKER_NAME = "adoption.json"

#: Suffixes that count as "code someone wrote before the harness arrived".
#: Deliberately narrow: a repo of notebooks and CSVs is not what this is about.
SOURCE_SUFFIXES = (
    ".py",
    ".r",
    ".jl",
    ".m",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".rs",
    ".go",
    ".java",
    ".scala",
    ".sh",
    ".cu",
)

#: Paths that are the harness itself, or noise, rather than the project.
_IGNORED_PARTS = frozenset(
    {".harness", ".git", "__pycache__", ".venv", "venv", "node_modules", "build", "dist"}
)


@dataclasses.dataclass
class Adoption:
    """How the harness arrived in a project that already had code."""

    adopted_at: str = ""
    commit: str = ""
    source_files: int = 0
    #: A few real paths, so the record is checkable rather than just a count.
    samples: list[str] = dataclasses.field(default_factory=list)

    @property
    def is_adoption(self) -> bool:
        return self.source_files > 0


def marker_path(root: str | Path = ".") -> Path:
    return get_harness_dir(root) / MARKER_NAME


def count_existing_source(root: str | Path = ".", limit: int = 5000) -> tuple[int, list[str]]:
    """Count pre-existing source files, and keep a few paths as evidence."""
    root = Path(root)
    count = 0
    samples: list[str] = []
    for path in root.rglob("*"):
        if count >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if _IGNORED_PARTS & set(path.relative_to(root).parts):
            continue
        count += 1
        if len(samples) < 5:
            samples.append(str(path.relative_to(root)))
    return count, samples


def _git_commit(root: Path) -> str:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - git absent
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def record(root: str | Path = ".") -> Adoption | None:
    """Note that the harness landed on an existing codebase. None if it did not."""
    root = Path(root).resolve()
    count, samples = count_existing_source(root)
    if not count:
        return None
    adoption = Adoption(
        adopted_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        commit=_git_commit(root),
        source_files=count,
        samples=samples,
    )
    path = marker_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dataclasses.asdict(adoption), indent=2) + "\n", encoding="utf-8")
    return adoption


def read(root: str | Path = ".") -> Adoption | None:
    path = marker_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return Adoption(
        adopted_at=str(data.get("adopted_at", "")),
        commit=str(data.get("commit", "")),
        source_files=int(data.get("source_files", 0) or 0),
        samples=[str(s) for s in data.get("samples", []) or []],
    )


def next_steps(root: str | Path = ".") -> list[str]:
    """The order the adoption path actually needs, when it differs from greenfield."""
    from harness import planners as planners_mod
    from harness import project as project_mod

    steps: list[str] = []
    with contextlib.suppress(project_mod.ProjectError):
        if project_mod.load_project_context(root).is_empty:
            steps.append(
                "harness project init"
                "                       # what a Planner must know about this project"
            )
    if not planners_mod.list_planners(root):
        steps.append(
            "harness create -n <name> --model <model>        # a Planner outlives this experiment"
        )
    steps.append("harness exp start <name> --planner <name>       # and plan the adoption there")
    return steps


# ---------------------------------------------------------------------------
# What a Planner is told


#: Conditions a module boundary has to satisfy. These are not style advice —
#: each one follows from something the harness can or cannot enforce, and a
#: boundary that fails one of them produces a module the harness cannot judge.
BOUNDARY_CONDITIONS = [
    (
        "You can write its contract",
        "typed inputs and outputs. If you cannot name them, it is an internal "
        "detail of something larger, not a module.",
    ),
    (
        "You can verify it cheaply",
        "acceptance runs without the full pipeline, the cluster, or the dataset. "
        "Anything that needs those belongs in the integration spec instead — a "
        "Worker is capped at its timeout, so an expensive check cannot gate a module.",
    ),
    (
        "It has exactly one owner",
        "two modules declaring the same deliverable means the decomposition is "
        "wrong; `plan validate` rejects it.",
    ),
    (
        "It changes at its own rate",
        "things that always change together should stay together, or every "
        "experiment touches two modules to make one change.",
    ),
    (
        "You can observe its boundary today",
        "you can capture what crosses it *before* you move anything. This is the "
        "one people skip, and without it a refactor cannot be checked — only hoped for.",
    ),
]


def brief_lines(adoption: Adoption, root: str | Path = ".") -> list[str]:
    """The adoption section of a Planner briefing. Guidance, not a procedure."""
    lines = [
        "## This project predates the harness",
        "",
        f"The harness arrived here on {adoption.adopted_at or 'an unrecorded date'}"
        + (f" at commit `{adoption.commit[:12]}`" if adoption.commit else "")
        + f", on top of {adoption.source_files} existing source file(s)"
        + (f" — {', '.join(f'`{s}`' for s in adoption.samples[:3])}, …" if adoption.samples else "")
        + ".",
        "",
        "None of that code is covered by a contract, an acceptance check, or a plan.",
        "Making it verifiable is work like any other work: settle the question with",
        "the researcher, decompose it, give each module a contract and acceptance,",
        "and prove it. **How to decompose it is yours to decide** — nothing here is a",
        "prescribed pipeline, and a codebase you have read beats a rule you have not.",
        "",
        "### What has to be true of a module boundary",
        "",
    ]
    lines += [f"{i}. **{title}** — {why}" for i, (title, why) in enumerate(BOUNDARY_CONDITIONS, 1)]
    lines += [
        "",
        "### One ordering principle",
        "",
        "In research code the artifact of record is a *measurement*, so the test of a",
        "refactor is not that the tests still pass — it is that the published numbers",
        "did not move. That inverts the usual order: **pin the behaviour you must not",
        "change before you change anything**, and decide the tolerance while you still",
        "have nothing invested in the answer.",
        "",
        "Two things worth checking early, because both are cheap and both have been",
        "expensive to discover late:",
        "",
        "- **Does the code your tests import match the code your entry point runs?**",
        "  A green suite covering an implementation nobody executes is worse than no",
        "  suite, because it reads as evidence.",
        "- **Where is the configuration actually read?** Scattered environment or global",
        "  reads are what let two callers disagree about what the same arm means.",
        "",
    ]
    return lines
