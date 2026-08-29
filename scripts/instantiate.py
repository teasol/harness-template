#!/usr/bin/env python
"""Instantiate this template into a new project.

Usage::

    python scripts/instantiate.py --name my-awesome-project [--drop-demo]

Replaces every occurrence of ``harness-template`` with the new project slug in
the key text files, optionally removes the shipped orchestration demo, then
prints next steps.

The demo (``plans/demo-pipeline.yaml`` and the modules it builds) is a working
example, but a new project inherits its *finished task board* — someone else's
completed work sitting on your board. ``--drop-demo`` removes it. The one-step
smoke test (``configs/demo.yaml``) is kept either way, so ``make verify`` still
proves the harness itself works on day one.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

#: The orchestration example: a plan, its integration spec, its task board,
#: and the modules it builds. Removed by --drop-demo.
DEMO_PATHS = [
    "plans/demo-pipeline.yaml",
    "configs/demo-pipeline.yaml",
    "tasks/data-gen.task.yaml",
    "tasks/stats.task.yaml",
    "src/demo_pipeline",
]

TARGETS = [
    "pyproject.toml",
    "README.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "environment.yml",
    "docs/architecture.md",
    "docs/verification.md",
    "docs/reproducibility.md",
]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise SystemExit("error: name produces an empty slug")
    return slug


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--name", required=True, help="New project name (e.g. my-awesome-project)")
    parser.add_argument(
        "--drop-demo",
        action="store_true",
        help="Remove the shipped orchestration demo (plan, integration spec, task board, modules)",
    )
    args = parser.parse_args()

    new_name = slugify(args.name)
    root = Path(__file__).resolve().parent.parent

    changed = []
    for rel in TARGETS:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "harness-template" in text:
            path.write_text(text.replace("harness-template", new_name), encoding="utf-8")
            changed.append(rel)

    print(f"Renamed 'harness-template' -> '{new_name}' in {len(changed)} file(s):")
    for rel in changed:
        print(f"  - {rel}")

    if args.drop_demo:
        removed = _drop_demo(root)
        print(f"\nRemoved the orchestration demo ({len(removed)} path(s)):")
        for rel in removed:
            print(f"  - {rel}")
        print(
            "  configs/demo.yaml and scripts/demo_step.py were kept: `make verify`",
            "  still proves the harness works before you write anything.",
            sep="\n",
        )

    steps = [
        "  1. Review the diff and commit.",
        "  2. Update README.md and AGENTS.md with project-specific details.",
        "  3. Delete scripts/instantiate.py (no longer needed).",
        "  4. Run: make setup && make verify && make test",
        "  5. Start your first experiment:",
        "       python -m harness exp start <hypothesis-name>",
        "       python -m harness planner brief <hypothesis-name> --register <label>",
    ]
    if not args.drop_demo:
        steps.insert(
            0,
            "  0. The shipped demo's finished tasks are on your board"
            " (`harness task list`).\n"
            "     Re-run with --drop-demo to remove them, or keep them as a"
            " worked example.",
        )
    print("\nNext steps:", *steps, sep="\n")


def _drop_demo(root: Path) -> list[str]:
    """Remove the orchestration example, keeping the harness smoke test."""
    removed = []
    for rel in DEMO_PATHS:
        path = root / rel
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(rel)
        elif path.is_file():
            path.unlink()
            removed.append(rel)
    # The Makefile's PLAN default now points at a plan that no longer exists.
    makefile = root / "Makefile"
    if makefile.is_file():
        text = makefile.read_text(encoding="utf-8")
        makefile.write_text(
            text.replace(
                "PLAN ?= plans/demo-pipeline.yaml",
                "PLAN ?= plans/CHANGE-ME.yaml   # set this to your plan",
            ),
            encoding="utf-8",
        )
    return removed


if __name__ == "__main__":
    main()
