"""Tests for project context — what a Planner is told before it plans.

The motivating incident: a status document's summary line read
``SMAD4 0.4282 -> 0.5483`` (a single-branch figure) while the authoritative
per-task table said the arm scored ``0.4465``. A Planner reading the summary
nearly reported a collapsed branch. Nothing was wrong with the documents — the
Planner simply had no way to know which one was the source of truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.project import (
    ProjectError,
    brief_lines,
    config_path,
    load_project_context,
    missing_docs,
    write_template,
)

CONTEXT = """\
project:
  docs:
    authority: docs/status.md
    architecture: docs/arch.md
  report_format: docs/status.md
  environment: scripts/node_env.sh
  python: /opt/env/bin/python
  conventions:
    - "No t-tests on deterministic arms."
    - "Closed axes are not retried."
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "configs").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs" / "status.md").write_text("numbers", encoding="utf-8")
    (tmp_path / "docs" / "arch.md").write_text("arch", encoding="utf-8")
    (tmp_path / "scripts" / "node_env.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "configs" / "project.yaml").write_text(CONTEXT, encoding="utf-8")
    return tmp_path


def test_absent_context_is_not_an_error(tmp_path: Path) -> None:
    """A fresh template has no project to describe yet."""
    context = load_project_context(tmp_path)
    assert context.is_empty
    assert context.authority_doc == ""


def test_context_round_trips(project: Path) -> None:
    context = load_project_context(project)
    assert context.authority_doc == "docs/status.md"
    assert context.docs["architecture"] == "docs/arch.md"
    assert context.python == "/opt/env/bin/python"
    assert len(context.conventions) == 2


def test_invalid_context_is_reported_not_ignored(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "project.yaml").write_text(
        "project:\n  docs: [not, a, mapping]\n", encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="must be a mapping"):
        load_project_context(tmp_path)


def test_missing_documents_are_flagged(project: Path) -> None:
    """Pointing a Planner at a moved file is worse than pointing at nothing."""
    (project / "docs" / "arch.md").unlink()
    gaps = missing_docs(load_project_context(project), project)
    assert gaps == ["architecture: docs/arch.md"]


def test_brief_names_the_authority_and_the_conventions(project: Path) -> None:
    text = "\n".join(brief_lines(load_project_context(project), project))
    assert "Numbers of record: `docs/status.md`" in text
    assert "this one wins" in text
    # The failure mode that motivated this must be spelled out, not implied.
    assert "summary" in text
    assert "No t-tests on deterministic arms." in text
    assert "PROJECT_PYTHON" in text


def test_brief_warns_about_registered_but_missing_paths(project: Path) -> None:
    (project / "scripts" / "node_env.sh").unlink()
    text = "\n".join(brief_lines(load_project_context(project), project))
    assert "Registered but missing" in text
    assert "node_env.sh" in text


def test_empty_context_tells_you_how_to_create_one(tmp_path: Path) -> None:
    text = "\n".join(brief_lines(load_project_context(tmp_path), tmp_path))
    assert "project init" in text


def test_template_scaffolds_and_refuses_to_clobber(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    path = write_template(tmp_path)
    assert path == config_path(tmp_path)
    assert "authority" in path.read_text(encoding="utf-8")
    with pytest.raises(ProjectError, match="already exists"):
        write_template(tmp_path)
    assert write_template(tmp_path, force=True).is_file()
