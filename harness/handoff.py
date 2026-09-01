"""The document a new Planner session reads instead of being told.

The harness was built as if one Planner session lasts a project. It does not:
you go home, the context window fills, you open a different tool tomorrow. Every
one of those is a new session that knows nothing, and what it needs is not
another briefing to sit through — it is one path.

So this renders one. The rule it follows is the rule the rest of the harness
follows: **derive what can be derived, record only what cannot.** Plan state,
which module is next, what is blocked, which commands to run — all of that comes
out of the plan and task files at render time, so it cannot go stale and nobody
has to maintain it. What no file knows is the reasoning: why this approach and
not the obvious one, what was already tried and failed, what the session was in
the middle of. Those are recorded as they happen, in one line each, and they are
the entire reason this document is worth reading.

It lives at the top of the main working tree and is meant to be committed. A
handoff that only exists on the machine that wrote it hands nothing over.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from pathlib import Path

from harness import invocation
from harness import planners as planners_mod
from harness import plans as plans_mod

FILENAME = "HANDOFF.md"

#: Sections, in the order a new session needs them: where the work is, what it
#: was about to do, then what it must not redo.
_HEADINGS = {
    "next": "What the last session was doing",
    "decision": "Decisions already made",
    "dead-end": "Dead ends — do not retry these",
    "fact": "What else is known",
}


def handoff_path(root: str | Path = ".") -> Path:
    """Always the main working tree, even when called from inside a worktree.

    A plan's branch is never merged, so a handoff written inside its worktree
    would travel nowhere — the next machine fetches `main` and finds nothing.
    """
    return planners_mod.main_repo_root(root) / FILENAME


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _plan_section(root: Path, status: plans_mod.ProjectStatus) -> list[str]:
    """Where the work stands, entirely derived — never recorded, never stale."""
    lines: list[str] = ["## Where the work is", ""]
    lines.append(f"- Project root: `{root}`")

    if status.plans:
        for item in status.plans:
            lines += [
                f"- Plan **{item.name}** — {item.state}: {item.detail}",
                f"  - branch `{item.git_branch}`, worktree `.worktrees/{item.name}`",
                f"  - next: `{item.next_command}`",
            ]
    else:
        lines.append("- No plan has a worktree here.")

    dormant = plans_mod.dormant_plans(root)
    if dormant:
        lines += [
            "",
            "Plans that are on a branch here but have no worktree on this machine —",
            "this is what a fresh clone looks like, and the work is not lost:",
            "",
        ]
        lines += [f"- **{name}**: `{invocation.cmd(f'plan resume {name}')}`" for name in dormant]

    lines.append("")
    return lines


def _note_sections(root: Path) -> list[str]:
    """The half no file knows, in the order it is needed."""
    notes = planners_mod.all_notes(root)
    if not notes:
        return [
            "## Nothing has been recorded yet",
            "",
            "No decision, dead end or intent has been written down in this project,",
            "so everything below the derived state above is unknown to you and to",
            "whoever comes next. See *Recording as you go*.",
            "",
        ]

    lines: list[str] = []
    for kind in ("next", "decision", "dead-end", "fact"):
        of_kind = [note for note in notes if note.kind == kind]
        if not of_kind:
            continue
        lines += [f"## {_HEADINGS[kind]}", ""]
        if kind == "next":
            # Intent is state, not history: only the last one is true. The
            # earlier ones are kept in the registry but would only mislead here.
            latest = of_kind[-1]
            lines += [
                f"> {latest.text}",
                "",
                f"— {latest.by}"
                + (f", plan `{latest.plan}`" if latest.plan else "")
                + f", {latest.at}",
                "",
                "Nothing above is a promise that it was finished. Check the derived",
                "state first; if they disagree, the files are right.",
                "",
            ]
            continue
        for note in of_kind:
            where = f" [`{note.plan}`]" if note.plan else ""
            lines.append(f"- {note.text}{where} — {note.by}, {note.at[:10]}")
        lines.append("")
    return lines


def _reading_section(root: Path) -> list[str]:
    """Only paths that exist: a pointer to a missing file is worse than none."""
    harness_dir = root / ".harness"
    base = harness_dir if harness_dir.is_dir() else root
    candidates = [
        (base / "agents" / "planner.md", "your role contract — read it first"),
        (root / "AGENTS.md", "ground rules for every agent here"),
        (base / "configs" / "project.yaml", "what this project expects of you"),
    ]
    present = [(path, why) for path, why in candidates if path.is_file()]
    if not present:
        return []
    lines = ["## Read these", ""]
    lines += [f"- `{path.relative_to(root)}` — {why}" for path, why in present]
    lines.append("")
    return lines


def _recording_section() -> list[str]:
    return [
        "## Recording as you go",
        "",
        "Not at the end — there may not be an end. A session that fills its",
        "context or a laptop that closes leaves whatever was written down, and",
        "nothing else.",
        "",
        "```bash",
        invocation.cmd('note "chose X over Y because Z" --decision'),
        invocation.cmd('note "tried A, it fails because B" --dead-end'),
        invocation.cmd('handoff --next "what you are in the middle of"'),
        "```",
        "",
        "One line each. Rediscovering a dead end costs a session; recording it",
        "costs a sentence. This file is regenerated whenever the work moves, so",
        "you never have to edit it — and editing it by hand is overwritten.",
        "",
    ]


def render(root: str | Path = ".", cwd: str | Path | None = None) -> str:
    """The whole document."""
    root = planners_mod.main_repo_root(root)
    cwd = Path(cwd).resolve() if cwd else None

    status = plans_mod.project_status(root, cwd=cwd)
    lines = [
        f"# Handoff — {status.project_name if status.instantiated else root.name}",
        "",
        f"Generated by `{invocation.cmd('handoff')}` at {_stamp()}. Regenerated",
        "whenever the work moves, so it is never older than the state it",
        "describes. Read it top to bottom: it is meant to replace being told.",
        "",
    ]
    lines += _plan_section(root, status)
    lines += _note_sections(root)
    lines += _reading_section(root)
    lines += _recording_section()
    return "\n".join(lines).rstrip() + "\n"


def write(root: str | Path = ".", cwd: str | Path | None = None) -> Path:
    """Render to the file, and return where it went."""
    target = handoff_path(root)
    target.write_text(render(root, cwd=cwd), encoding="utf-8")
    return target


def _worth_handing_over(root: Path) -> bool:
    """Whether this project has anything a next session would need told.

    A project with no Planner, no plan in flight and no plan on a branch has
    nothing to hand over, and writing a document that says so into somebody's
    working directory is noise — the file would also be the first thing a fresh
    clone of a template inherits, describing a project that is not theirs.
    """
    # `list_plans` shells out to git, and a project need not be a repository:
    # "not a repository" means no plans, not "nothing to hand over".
    with contextlib.suppress(plans_mod.WorkPlanError):
        if plans_mod.list_plans(root) or plans_mod.dormant_plans(root):
            return True
    return bool(planners_mod.list_planners(root))


def refresh(root: str | Path = ".") -> Path | None:
    """Rewrite the handoff after the work moved, without ever being the reason
    a command fails.

    Called from the commands that change state, which is what makes the document
    survive a session that ends abruptly instead of tidily. A handoff that could
    fail `task done` would be a worse trade than a stale handoff, so every
    failure here is swallowed.
    """
    try:
        target = handoff_path(root)
        # An existing handoff is always refreshed: leaving a stale one is worse
        # than having none, because it will be read as current.
        if not target.exists() and not _worth_handing_over(Path(root)):
            return None
        return write(root)
    except Exception:  # noqa: BLE001 - never break the caller over a document
        return None
