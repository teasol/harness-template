#!/usr/bin/env python3
"""Refresh the checked-in agent configuration from its single source.

Two copies of each file exist for a reason — the package ships what `harness
init` installs, and this repository is itself a harness project — and they
drifted for three releases, leaving the copy users receive poorer than the copy
maintained here. `tests/test_config_sources.py` fails when they diverge; this is
what you run to make them agree again.

The header of `agents.yaml` is written by `harness setup` from
``harness.setup.HEADER``, so this rewrites only that header and leaves every
configured value alone: running it in a real project does not reset which agent
runs which tier. The platform presets have nothing project-specific in them at
all, so the packaged file is copied over the root one wholesale.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from harness.setup import HEADER  # noqa: E402 - after sys.path, by design

PACKAGED = REPO / "harness" / "templates" / "configs"
ROOT = REPO / "configs"


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


def copy_presets() -> bool:
    """The packaged presets are the source of record; the root copy follows."""
    src, dst = PACKAGED / "agent-platforms.yaml", ROOT / "agent-platforms.yaml"
    if src.read_bytes() == dst.read_bytes():
        return False
    shutil.copyfile(src, dst)
    return True


def main() -> int:
    changed = [
        str(path.relative_to(REPO))
        for path, did in (
            (ROOT / "agents.yaml", refresh_header(ROOT / "agents.yaml")),
            (PACKAGED / "agents.yaml", refresh_header(PACKAGED / "agents.yaml")),
            (ROOT / "agent-platforms.yaml", copy_presets()),
        )
        if did
    ]
    print("\n".join(f"updated {name}" for name in changed) or "already in sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
