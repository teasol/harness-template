"""Tests that keep the shipped configuration and its documentation honest.

The configuration a project receives lives in one place, `harness/templates/`,
and is copied out by `harness init`. It did not use to: a second copy sat at
this repository's root, because the repository was treated as a harness project
too, and for three releases the root copy was maintained while the copy users
actually receive was not — so the file a project got was the poorer of the two.
The duplicates are gone, and `agents.yaml` has a source above even the shipped
file: `harness setup` generates its header from `harness.setup.HEADER`, so the
checked-in file is only that constant's output.

These tests also gate a bug that shipped twice: a placeholder documented in a
comment that nothing substitutes. `render_command` resolves the command template
with `str.format`, so a name it does not pass raises `KeyError` at spawn time —
after the model has been chosen and the brief written.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from harness import setup as setup_mod
from harness.worker import AgentConfig, render_command

REPO = Path(__file__).resolve().parent.parent
PACKAGED_CONFIGS = REPO / "harness" / "templates" / "configs"

SYNC = "make sync-configs"


def test_the_repository_keeps_no_second_copy_of_the_configuration() -> None:
    """One copy cannot drift from itself, which is why the duplicates went.

    This repository is the package, not a harness project: it does not run the
    harness on itself, so it has no reason to hold configuration of its own.
    """
    for stray in ("configs/agents.yaml", "configs/agent-platforms.yaml"):
        assert not (REPO / stray).exists(), (
            f"{stray} is back. The shipped copy under harness/templates/configs/ is "
            "the only one; a second copy is what drifted for three releases."
        )


def test_the_shipped_agents_yaml_carries_the_current_header() -> None:
    """`setup.HEADER` is the source; the checked-in file is only its output."""
    text = (PACKAGED_CONFIGS / "agents.yaml").read_text(encoding="utf-8")
    assert text.startswith(setup_mod.HEADER), (
        "harness/templates/configs/agents.yaml has a stale header. It is written "
        f"by `harness setup`, so regenerate it rather than editing it: {SYNC}"
    )


# ---------------------------------------------------------------------------
# documented placeholders must be placeholders that exist

PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def _documented(text: str) -> tuple[set[str], set[str]]:
    """The placeholders a comment block claims, split into always and task-only."""
    always: set[str] = set()
    task_only: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        if "on every invocation" in line:
            always |= set(PLACEHOLDER_RE.findall(line))
        elif "task runs only" in line:
            task_only |= set(PLACEHOLDER_RE.findall(line))
    return always, task_only


DOCUMENTING_FILES = [
    PACKAGED_CONFIGS / "agents.yaml",
    PACKAGED_CONFIGS / "agent-platforms.yaml",
]


def _render(template: str, **extra: str) -> str:
    config = AgentConfig(adapter="cli", model="m", effort="high", session="s", command=template)
    return render_command(config, Path("."), Path("brief.md"), **extra)


def test_the_header_documents_placeholders_at_all() -> None:
    """A parser that silently found nothing would make the tests below vacuous."""
    always, task_only = _documented(setup_mod.HEADER)
    assert always and task_only


@pytest.mark.parametrize("path", DOCUMENTING_FILES, ids=lambda p: p.name)
def test_documented_placeholders_are_the_ones_the_harness_substitutes(path: Path) -> None:
    """`{experiment}` and `{plan}` were both documented; neither ever existed."""
    always, task_only = _documented(path.read_text(encoding="utf-8"))
    assert always, f"{path.relative_to(REPO)} documents no always-available placeholders"

    template = " ".join(f"{{{name}}}" for name in sorted(always))
    _render(template)  # a name render_command does not pass raises KeyError here

    if task_only:
        template = " ".join(f"{{{name}}}" for name in sorted(always | task_only))
        _render(template, task_id="widget", task_file="tasks/widget.yaml")


def test_both_files_document_the_same_placeholders() -> None:
    """Two lists that disagree mean one of them is wrong, and no one knows which."""
    documented = {
        str(path.relative_to(REPO)): tuple(
            sorted(group) for group in _documented(path.read_text(encoding="utf-8"))
        )
        for path in DOCUMENTING_FILES
    }
    distinct = {tuple(tuple(group) for group in value) for value in documented.values()}
    assert len(distinct) == 1, documented


def test_an_undocumented_placeholder_still_fails_loudly() -> None:
    """The guarantee the tests above rest on: format() does not ignore unknowns."""
    with pytest.raises(KeyError):
        _render("{experiment}")
