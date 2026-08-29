"""The recorded version must be the real one.

`0.2.0` shipped without bumping `pyproject.toml` or `harness.__version__`, so
every run made during it stamped its provenance `0.1.0`. In a tool whose job is
reproducibility, provenance that names the wrong harness is not a cosmetic bug:
it is the field you would use to explain why two runs disagree.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

import harness
from harness.reproducibility import collect_provenance

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def test_package_version_matches_pyproject() -> None:
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert harness.__version__ == declared, (
        f"harness.__version__ is {harness.__version__} but pyproject says {declared}; "
        "provenance would record the wrong harness for every run"
    )


def test_current_version_has_a_changelog_entry() -> None:
    """A released version nobody wrote down is a version nobody can look up."""
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG.read_text("utf-8"), re.MULTILINE)
    assert harness.__version__ in released, (
        f"no CHANGELOG section for {harness.__version__}; found {released[:5]}"
    )


def test_provenance_reports_that_version() -> None:
    assert collect_provenance(".").get("harness_version") == harness.__version__
