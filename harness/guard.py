"""Containment: what a Worker may change, enforced rather than requested.

``AGENTS.md`` already tells Workers never to touch ``harness/``, the plan, or
another module's files. That rule held only as long as the agent chose to
follow it. This module makes it hold regardless.

Two boundaries are watched around every Worker invocation:

**The harness itself.** An agent whose acceptance step fails for an
infrastructural reason can "fix" it by editing the harness instead of its own
module — and then every guarantee the harness offers is guaranteeing itself.
This was observed in practice: a Worker patched ``runner.py`` mid-attempt,
which both escaped its sandbox and silently invalidated the retry loop, since
the already-running harness process kept executing the code it had imported at
startup. Modifying the harness is therefore fatal, never a retry.

**Declared deliverables.** A module's ``deliverables`` list is its contract. A
Worker that modifies a tracked file it never declared has changed something
nobody is checking — the exact shape of an unnoticed regression. Modifying an
undeclared tracked file is fatal; creating undeclared new files is reported but
allowed, since scratch output is usually harmless.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import harness as _harness_package

#: Files inside the harness package worth watching. Bytecode is derived, and
#: data files are not what an agent reaches for when it wants to change
#: behaviour.
_WATCHED_SUFFIXES = (".py",)


class GuardViolation(RuntimeError):
    """Raised when a Worker changed something outside its contract."""


def harness_package_dir() -> Path:
    """Absolute path of the harness package currently being executed."""
    return Path(_harness_package.__file__).resolve().parent


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_tree(root: Path, suffixes: tuple[str, ...] = _WATCHED_SUFFIXES) -> dict[str, str]:
    """Map every watched file under ``root`` to its sha256."""
    snapshot: dict[str, str] = {}
    if not root.is_dir():
        return snapshot
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or not path.is_file():
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        try:
            snapshot[str(path)] = _hash_file(path)
        except OSError:  # pragma: no cover - unreadable file, nothing to compare
            continue
    return snapshot


def snapshot_harness() -> dict[str, str]:
    """Snapshot the harness package so self-modification cannot pass unnoticed."""
    return snapshot_tree(harness_package_dir())


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Paths that differ between two snapshots (added, removed, or edited)."""
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def _git(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - git absent
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def repo_changes(root: Path) -> tuple[set[str], set[str]]:
    """Return ``(modified_tracked, created_untracked)`` paths, repo-relative.

    Empty sets when ``root`` is not a git repository — the guard degrades to
    watching the harness only rather than failing the run.
    """
    out = _git(root, "status", "--porcelain")
    if out is None:
        return set(), set()
    modified: set[str] = set()
    created: set[str] = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        # Renames read "R  old -> new"; the new path is what now exists.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if code == "??":
            created.add(path)
        else:
            modified.add(path)
    return modified, created


def _is_within(path: str, prefixes: tuple[str, ...]) -> bool:
    normalized = path.lstrip("./")
    return any(normalized == p or normalized.startswith(p.rstrip("/") + "/") for p in prefixes)


def undeclared_changes(
    root: Path,
    deliverables: list[str],
    before_modified: set[str],
    before_created: set[str],
    exempt_prefixes: tuple[str, ...] = ("results/", ".harness/", "tasks/", "logs/"),
) -> tuple[list[str], list[str]]:
    """Compare repo state against the module's declared deliverables.

    Returns ``(modified_undeclared, created_undeclared)``. Paths already dirty
    before the Worker ran are excluded, so a Worker is never blamed for the
    state it inherited. Harness bookkeeping directories are exempt because the
    harness writes into them itself during the attempt.
    """
    declared = {d.lstrip("./") for d in deliverables}
    modified_now, created_now = repo_changes(root)

    modified_new = sorted(
        p
        for p in modified_now - before_modified
        if p.lstrip("./") not in declared and not _is_within(p, exempt_prefixes)
    )
    created_new = sorted(
        p
        for p in created_now - before_created
        if p.lstrip("./") not in declared and not _is_within(p, exempt_prefixes)
    )
    return modified_new, created_new
