#!/usr/bin/env python
"""Instantiate this template into a new project.

Usage::

    python scripts/instantiate.py --name my-awesome-project

Replaces every occurrence of ``harness-template`` with the new project slug in
the key text files, then prints next steps.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

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
    print(
        "\nNext steps:",
        "  1. Review the diff and commit.",
        "  2. Update README.md and AGENTS.md with project-specific details.",
        "  3. Delete scripts/instantiate.py (no longer needed).",
        "  4. Run: make setup && make verify",
        sep="\n",
    )


if __name__ == "__main__":
    main()
