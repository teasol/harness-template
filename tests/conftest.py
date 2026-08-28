"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def check_root(tmp_path: Path) -> Path:
    """A temporary root directory for resolving check paths."""
    return tmp_path
