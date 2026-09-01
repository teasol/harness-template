"""Tests that keep the shipped configuration and its documentation honest.

Three copies of the agent configuration exist for good reasons — one in the
package that `harness init` installs, one at this repository's root because the
repository is also a harness project, and the header that `harness setup`
writes. They drifted: the root copies were maintained for three releases while
the copies users actually receive were not, so the file a project gets was the
poorer of the two. Prose is not testable, but *identity* is, and the drift these
tests catch is always identity drift.

They also gate a bug that has now shipped twice: a placeholder documented in a
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
ROOT_CONFIGS = REPO / "configs"

SYNC = "make sync-configs"


@pytest.mark.parametrize(
    "path",
    [ROOT_CONFIGS / "agents.yaml", PACKAGED_CONFIGS / "agents.yaml"],
    ids=["root", "packaged"],
)
def test_every_checked_in_agents_yaml_carries_the_current_header(path: Path) -> None:
    """`setup.HEADER` is the single source; a checked-in copy is only its output."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith(setup_mod.HEADER), (
        f"{path.relative_to(REPO)} has a stale header. It is written by "
        f"`harness setup`, so regenerate it rather than editing it: {SYNC}"
    )


def test_the_platform_presets_are_the_same_file_everywhere() -> None:
    """Presets are vendor data with nothing project-specific to differ about."""
    packaged = (PACKAGED_CONFIGS / "agent-platforms.yaml").read_bytes()
    root = (ROOT_CONFIGS / "agent-platforms.yaml").read_bytes()
    assert packaged == root, (
        "configs/agent-platforms.yaml and the copy inside the package have "
        f"diverged. The packaged file is the source of record: {SYNC}"
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
    ROOT_CONFIGS / "agents.yaml",
    PACKAGED_CONFIGS / "agents.yaml",
    ROOT_CONFIGS / "agent-platforms.yaml",
    PACKAGED_CONFIGS / "agent-platforms.yaml",
]


def _render(template: str, **extra: str) -> str:
    config = AgentConfig(adapter="cli", model="m", effort="high", session="s", command=template)
    return render_command(config, Path("."), Path("brief.md"), **extra)


def test_the_header_documents_placeholders_at_all() -> None:
    """A parser that silently found nothing would make the tests below vacuous."""
    always, task_only = _documented(setup_mod.HEADER)
    assert always and task_only


@pytest.mark.parametrize("path", DOCUMENTING_FILES, ids=lambda p: str(p.name) + ":" + p.parent.name)
def test_documented_placeholders_are_the_ones_the_harness_substitutes(path: Path) -> None:
    """`{experiment}` and `{plan}` were both documented; neither ever existed."""
    always, task_only = _documented(path.read_text(encoding="utf-8"))
    assert always, f"{path.relative_to(REPO)} documents no always-available placeholders"

    template = " ".join(f"{{{name}}}" for name in sorted(always))
    _render(template)  # a name render_command does not pass raises KeyError here

    if task_only:
        template = " ".join(f"{{{name}}}" for name in sorted(always | task_only))
        _render(template, task_id="widget", task_file="tasks/widget.yaml")


def test_the_two_files_document_the_same_placeholders() -> None:
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
