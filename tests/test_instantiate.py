"""Tests for turning the template into a project.

The demo is the template's own worked example. A project created from the
template must not inherit it — a finished task board belonging to nobody is
clutter at best and miscounted progress at worst — so removal is not optional.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEMO_PATHS = [
    "plans/demo-pipeline.yaml",
    "configs/demo-pipeline.yaml",
    "tasks/data-gen.task.yaml",
    "tasks/stats.task.yaml",
    "src/demo_pipeline",
]


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the copy's own script.

    instantiate.py rewrites the repository it lives in, not the working
    directory — running the checkout's copy against a temp dir would edit the
    checkout itself.
    """
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "instantiate.py"), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture()
def copy(tmp_path: Path) -> Path:
    """A file copy of the template, so tests never touch the checkout."""
    target = tmp_path / "template"
    shutil.copytree(
        ".",
        target,
        ignore=shutil.ignore_patterns(
            ".git",
            ".experiments",
            "results",
            "*.egg-info",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
        ),
    )
    return target


def test_name_is_required(copy: Path) -> None:
    result = _run(copy, "--exam-demo", "--help")
    assert result.returncode == 0
    result = _run(copy)
    assert result.returncode != 0
    assert "--name is required" in result.stderr


def test_instantiating_always_removes_the_demo(copy: Path) -> None:
    for rel in DEMO_PATHS:
        assert (copy / rel).exists(), f"fixture should start with {rel}"

    result = _run(copy, "--name", "my-project")

    assert result.returncode == 0, result.stderr
    for rel in DEMO_PATHS:
        assert not (copy / rel).exists(), f"{rel} should have been removed"
    # The smoke test survives, so `make verify` works on day one.
    assert (copy / "configs" / "demo.yaml").is_file()
    assert (copy / "scripts" / "demo_step.py").is_file()
    assert 'name = "my-project"' in (copy / "pyproject.toml").read_text(encoding="utf-8")
    # The Makefile no longer points PLAN at a plan that is gone.
    assert "plans/demo-pipeline.yaml" not in (copy / "Makefile").read_text(encoding="utf-8")


def test_instantiated_project_has_an_empty_board(copy: Path) -> None:
    _run(copy, "--name", "my-project")
    assert not list((copy / "tasks").glob("*.task.yaml"))


def test_exam_demo_runs_the_example_without_instantiating(copy: Path) -> None:
    result = _run(copy, "--exam-demo")

    assert result.returncode == 0, result.stderr + result.stdout
    for phrase in ("valid", "2/2 task(s) passed", "PASSED", "REPRODUCIBLE"):
        assert phrase in result.stdout, f"expected {phrase!r} in the walkthrough"
    # It demonstrates; it must not modify anything.
    assert (copy / "plans" / "demo-pipeline.yaml").is_file()
    assert 'name = "harness-template"' in (copy / "pyproject.toml").read_text(encoding="utf-8")


def test_exam_demo_explains_itself_when_the_demo_is_gone(copy: Path) -> None:
    _run(copy, "--name", "my-project")
    result = _run(copy, "--exam-demo")
    assert result.returncode == 1
    assert "removed on instantiation" in result.stdout
