"""Provenance: what produced a run, so a report can answer "how do I redo this?".

All that is left of a larger module. The seeding and hash-comparison machinery
that lived here belonged to a determinism gate the harness no longer owns: what
counts as reproducible differs by project, so a project that needs it writes it
as a checklist item of its own. Recording *which* commit, interpreter and
platform produced a result is not project-specific, and a report is unreadable
without it.
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _git(root: Path, *args: str) -> str | None:
    """Run a read-only git command in ``root``; return None if unavailable."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # Empty output is a real answer: `status --porcelain` prints nothing when
    # the worktree is clean. Only a non-zero exit means "unavailable".
    return proc.stdout.strip()


def collect_provenance(root: str | Path = ".") -> dict[str, Any]:
    """Describe *what* produced a run, so a report answers 'how do I redo this?'.

    Every field is best-effort: a missing git binary or a non-repo checkout
    yields ``None`` rather than failing the run.
    """
    root = Path(root)
    commit = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain")
    return {
        "harness_version": _harness_version(),
        "git_commit": commit,
        "git_branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": None if status is None else bool(status),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
    }


def _harness_version() -> str:
    from harness import __version__

    return __version__
