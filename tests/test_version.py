"""The recorded version must be the real one.

`0.2.0` shipped without bumping `pyproject.toml` or `harness.__version__`, so
every run made during it stamped its provenance `0.1.0`. In a tool whose job is
reproducibility, provenance that names the wrong harness is not a cosmetic bug:
it is the field you would use to explain why two runs disagree.
"""

from __future__ import annotations

import re
from pathlib import Path

import harness
from harness.reproducibility import collect_provenance

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def _declared_version() -> str:
    """Read `version` from pyproject's [project] table.

    Deliberately not `tomllib`: that is stdlib only from 3.11, and this project
    supports 3.10 — a version-consistency test that cannot run on the oldest
    supported interpreter defeats its own purpose. One field does not justify a
    dependency either, so it is read directly.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    section = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    assert section, "pyproject.toml has no [project] table"
    found = re.search(r"""^version\s*=\s*["'](?P<v>[^"']+)["']""", section.group(1), re.MULTILINE)
    assert found, "no version in pyproject's [project] table"
    return found.group("v")


def test_package_version_matches_pyproject() -> None:
    declared = _declared_version()
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
