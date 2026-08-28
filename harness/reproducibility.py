"""Utilities for deterministic execution: seeding, env vars, hashing."""

from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path


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
