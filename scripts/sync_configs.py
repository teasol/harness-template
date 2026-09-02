#!/usr/bin/env python3
"""Refresh the shipped agent configuration from its source in the code.

The header of `agents.yaml` is written by `harness setup` from
``harness.setup.HEADER``, so the checked-in file under `templates/` is
that constant's output and nothing else. Editing the file by hand puts it out of
step with what `setup` would write into a real project; this puts it back.

Only the header is rewritten — every configured value is preserved — so this is
also safe to point at a real project's `agents.yaml`.
`tests/test_config_sources.py` is what fails when they disagree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness.orchestrate.setup import HEADER  # noqa: E402 - after sys.path, by design

PACKAGED = REPO / "templates" / "configs" / "agents.yaml"


def refresh_header(path: Path) -> bool:
    """Put the current HEADER on an agents.yaml, keeping its configured body."""
    text = path.read_text(encoding="utf-8")
    body = yaml.safe_load(text) or {}
    rewritten = HEADER + yaml.safe_dump(
        body, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    if rewritten == text:
        return False
    path.write_text(rewritten, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    targets = [Path(arg) for arg in argv] or [PACKAGED]
    changed = [str(path) for path in targets if refresh_header(path)]
    print("\n".join(f"updated {name}" for name in changed) or "already in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
