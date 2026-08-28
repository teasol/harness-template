"""Utilities for deterministic execution: seeding, env vars, hashing."""

from __future__ import annotations

import hashlib
import os
import platform
import random
import subprocess
import sys
from pathlib import Path
from typing import Any


def file_sha256(path: str | Path) -> str:
    """Return the hex sha256 digest of a file (streamed)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def set_all_seeds(seed: int) -> None:
    """Seed every RNG that is importable (stdlib, numpy, torch)."""
    random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    try:  # pragma: no cover - depends on environment
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    try:  # pragma: no cover - depends on environment
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def deterministic_env(seed: int) -> dict[str, str]:
    """Env vars that improve determinism of child processes."""
    return {
        "PYTHONHASHSEED": str(seed),
        # Required by CUDA >= 10.2 for deterministic cuBLAS ops (torch).
        "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
    }


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
    return proc.stdout.strip() or None


def collect_provenance(root: str | Path = ".", seed: int | None = None) -> dict[str, Any]:
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
        "seed": seed,
    }


def _harness_version() -> str:
    from harness import __version__

    return __version__
