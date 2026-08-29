#!/usr/bin/env python
"""Instantiate this template into a new project.

Usage::

    python scripts/instantiate.py --exam-demo          # watch the demo run first
    python scripts/instantiate.py --name my-project    # then make it yours

Instantiating **always** removes the shipped orchestration demo. The demo
exists to develop and test the template itself; a project created from it
would inherit the demo's *finished task board* — someone else's completed work
sitting on your board, counted in no one's progress and cluttering every
listing. Nobody wants that in a real project, so it is not a choice.

Read it before it goes: ``--exam-demo`` runs the whole example end to end
(plan → tasks → workers → integration) and prints what happened, so you can
see the flow on real output rather than inferring it from documentation.

The one-step smoke test (``configs/demo.yaml`` + ``scripts/demo_step.py``) is
kept, so ``make verify`` still proves the harness works on day one.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

#: The orchestration example: a plan, its integration spec, its task board,
#: and the modules it builds. Always removed on instantiation.
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
    parser.add_argument("--name", help="New project name (e.g. my-awesome-project)")
    parser.add_argument(
        "--exam-demo",
        action="store_true",
        help="Run the shipped demo end to end and exit, without instantiating",
    )
    args = parser.parse_args()

    if args.exam_demo:
        raise SystemExit(_exam_demo(Path(__file__).resolve().parent.parent))
    if not args.name:
        parser.error("--name is required (or use --exam-demo to watch the demo first)")

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

    removed = _drop_demo(root)
    print(f"\nRemoved the orchestration demo ({len(removed)} path(s)):")
    for rel in removed:
        print(f"  - {rel}")
    print(
        "  It was the template's own worked example; a project should not start",
        "  with someone else's finished task board. Run --exam-demo on a fresh",
        "  clone of the template if you want to watch it again.",
        "  configs/demo.yaml and scripts/demo_step.py were kept, so `make verify`",
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
    print("\nNext steps:", *steps, sep="\n")


def _exam_demo(root: Path) -> int:
    """Run the shipped example end to end so the flow can be seen, not inferred."""
    plan = root / "plans" / "demo-pipeline.yaml"
    spec = root / "configs" / "demo-pipeline.yaml"
    if not plan.is_file():
        print(
            "The demo is not present in this checkout — it is removed on"
            " instantiation.\nClone the template itself to run it.",
        )
        return 1

    steps = [
        ("The plan the Planner would write", ["plan", "validate", str(plan)]),
        ("Its module board", ["task", "list"]),
        ("Re-verifying every finished module", ["task", "verify", "--all", "--status", "done"]),
        ("The integration check of the assembled whole", ["verify", "--spec", str(spec)]),
        ("Determinism: same inputs, same artifacts", ["reproduce", "--spec", str(spec)]),
    ]
    from harness.cli import main as harness_main

    for index, (title, argv) in enumerate(steps, start=1):
        print(f"\n{'=' * 70}\n{index}. {title}\n   $ harness {' '.join(argv)}\n{'=' * 70}")
        code = harness_main(argv)
        if code != 0:
            print(f"\nDemo step {index} failed (exit {code}).", file=sys.stderr)
            return code

    print(
        f"\n{'=' * 70}",
        "That is the whole shape: a plan declares modules with contracts and",
        "machine-checkable acceptance; tasks carry them to Workers; the harness",
        "judges the result; the integration spec checks the assembled whole; the",
        "determinism gate proves it reproduces.",
        "",
        "Your project runs the same flow inside an experiment worktree:",
        "  harness exp start <hypothesis>",
        "  harness planner brief <hypothesis> --register <label>",
        "",
        "Ready? python scripts/instantiate.py --name <your-project>",
        sep="\n",
    )
    return 0


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
