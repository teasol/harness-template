"""Planners that outlive one plan.

A Planner spends its first hour learning the project: where the numbers of
record live, which interpreter has the dependencies, which arms are already
closed, that an empty checkpoint path is correct here rather than a bug. Then
the plan ends and all of it is discarded, and the next Planner pays the
same hour — and makes the same first-time mistakes, because the knowledge that
would have prevented them was never written anywhere.

So a Planner is a thing with a name and a memory, and plans hang off it:

    harness create -n icf --model claude-opus-5 --effort high
    harness plan new baseline --planner icf
    harness planner note icf --add "ICF_CKPT is empty on this node; that is correct."

The registry lives in the *main* repository, not in a plan's worktree, so
every plan under a Planner reads and appends to the same memory. Notes
are the Planner's own operational findings; durable facts about the project
belong in ``project.yaml`` (see :mod:`harness.project`), which the user
owns. The split matters: one is a lab notebook, the other is policy.
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from harness import invocation

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DIR_NAME = "planners"


class PlannerError(ValueError):
    """Raised when a Planner cannot be created, found, or read."""


#: What a note is for. The kinds exist because they are read at different
#: moments: a dead end is read *before* trying something, a decision *before*
#: reopening it, and the next-intent only ever matters as the latest one.
KINDS = ("fact", "decision", "dead-end", "next")


@dataclasses.dataclass
class Note:
    """One thing a Planner learned, with when it learned it and who learned it."""

    at: str
    text: str
    plan: str = ""
    kind: str = "fact"
    #: Filled in when the note is read back out of a registry, so a handoff can
    #: say who said it — sessions change, and "who" is part of how much weight
    #: a note carries.
    by: str = ""


@dataclasses.dataclass
class Planner:
    """A named Planner: what it runs on, what it has driven, what it knows."""

    name: str
    model: str = ""
    effort: str = ""
    created_at: str = ""
    plans: list[str] = dataclasses.field(default_factory=list)
    notes: list[Note] = dataclasses.field(default_factory=list)
    path: Path | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main_repo_root(root: str | Path = ".") -> Path:
    """The main working tree, even when called from inside a plan's worktree.

    A worktree has its own directory but shares the repository. The Planner
    registry belongs to the repository, so every plan under one Planner
    appends to the same memory rather than to a copy that dies with the plan.
    """
    root = Path(root).resolve()
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - git absent
        return root
    if proc.returncode != 0 or not proc.stdout.strip():
        return root
    common = Path(proc.stdout.strip())
    # `.../repo/.git` -> `.../repo`; a bare repo has no parent worth guessing at.
    return common.parent if common.name == ".git" else root


def planners_dir(root: str | Path = ".") -> Path:
    base = main_repo_root(root)
    harness_dir = base / ".harness"
    return (harness_dir if harness_dir.is_dir() else base) / DIR_NAME


def planner_path(name: str, root: str | Path = ".") -> Path:
    return planners_dir(root) / f"{name}.yaml"


def list_planners(root: str | Path = ".") -> list[Planner]:
    directory = planners_dir(root)
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            out.append(_from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path))
        except PlannerError:
            continue
    return out


def _from_dict(data: dict, path: Path) -> Planner:
    entry = data.get("planner", data)
    if not isinstance(entry, dict) or not entry.get("name"):
        raise PlannerError(f"{path} is not a planner record")
    notes = []
    for raw in entry.get("notes", []) or []:
        if isinstance(raw, dict) and raw.get("text"):
            kind = str(raw.get("kind", "fact") or "fact")
            notes.append(
                Note(
                    at=str(raw.get("at", "")),
                    text=str(raw["text"]),
                    plan=str(raw.get("plan", "")),
                    kind=kind if kind in KINDS else "fact",
                    by=str(entry["name"]),
                )
            )
    return Planner(
        name=str(entry["name"]),
        model=str(entry.get("model", "") or ""),
        effort=str(entry.get("effort", "") or ""),
        created_at=str(entry.get("created_at", "") or ""),
        plans=[str(e) for e in entry.get("plans", []) or []],
        notes=notes,
        path=path,
    )


def save(planner: Planner) -> Path:
    if planner.path is None:
        raise PlannerError(f"planner '{planner.name}' has no backing file")
    planner.path.parent.mkdir(parents=True, exist_ok=True)
    planner.path.write_text(
        yaml.safe_dump(
            {
                "planner": {
                    "name": planner.name,
                    "model": planner.model,
                    "effort": planner.effort,
                    "created_at": planner.created_at,
                    "plans": list(planner.plans),
                    "notes": [
                        {"at": n.at, "text": n.text, "plan": n.plan, "kind": n.kind}
                        for n in planner.notes
                    ],
                }
            },
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    return planner.path


def load(name: str, root: str | Path = ".") -> Planner:
    path = planner_path(name, root)
    if not path.is_file():
        known = [p.name for p in list_planners(root)]
        raise PlannerError(f"no planner '{name}'. known: {known or '(none)'}")
    return _from_dict(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, path)


def exists(name: str, root: str | Path = ".") -> bool:
    return planner_path(name, root).is_file()


def create(name: str, model: str = "", effort: str = "", root: str | Path = ".") -> Planner:
    """Register a Planner.

    The model may be unknown at this point and that is legitimate: a manual
    Planner is a session a person opens later, so nobody can name its model in
    advance. Requiring it here blocked creation outright for exactly the tier
    where it is unknowable.

    Knowing the model still matters — two runs planned by different models are
    not the same plan — but the place to insist on it is the report, which
    already refuses to call a run comparable when the model is missing. Record
    it whenever it becomes known with :func:`set_model`.
    """
    if not NAME_RE.match(name):
        raise PlannerError(
            f"invalid planner name '{name}' — use lowercase letters, digits, and hyphens"
        )
    path = planner_path(name, root)
    if path.exists():
        raise PlannerError(f"planner '{name}' already exists: {path}")
    planner = Planner(name=name, model=model, effort=effort, created_at=_now(), path=path)
    save(planner)
    return planner


def set_model(name: str, model: str, effort: str | None = None, root: str | Path = ".") -> Planner:
    """Record what a Planner turned out to be running on.

    For a manual Planner this is how the gap left at creation gets closed: the
    session that opened knows what it is, and says so.
    """
    if not model.strip():
        raise PlannerError("a model is required — that is the whole point of this command")
    planner = load(name, root)
    planner.model = model.strip()
    if effort is not None:
        planner.effort = effort
    save(planner)
    return planner


def add_note(
    name: str, text: str, plan: str = "", root: str | Path = ".", kind: str = "fact"
) -> Planner:
    """Append something this Planner learned, so the next one starts with it."""
    if not text.strip():
        raise PlannerError("a note needs text")
    if kind not in KINDS:
        raise PlannerError(f"unknown note kind '{kind}'. available: {', '.join(KINDS)}")
    planner = load(name, root)
    planner.notes.append(Note(at=_now(), text=text.strip(), plan=plan, kind=kind))
    save(planner)
    return planner


def all_notes(root: str | Path = ".") -> list[Note]:
    """Every note in the project, oldest first, whoever recorded it.

    Notes hang off a Planner because a Planner is what accumulates them, but
    they are read by *whoever is here now* — and that is often a different
    Planner, because switching machine or switching tool means a new model and
    therefore a new registration. Scoping the read to one name would lose the
    trail at exactly the moment it is most needed.
    """
    notes = [note for planner in list_planners(root) for note in planner.notes]
    return sorted(notes, key=lambda n: n.at)


def owner_of_plan(plan: str, root: str | Path = ".") -> str:
    """Which Planner drove this plan, or '' if nothing claims it."""
    for planner in list_planners(root):
        if plan in planner.plans:
            return planner.name
    return ""


def infer_planner(root: str | Path = ".", plan: str = "") -> str:
    """Who is speaking, when nobody said.

    A session that has just been handed a document does not know its own
    registered name, and asking it to pass one it would have to guess at is how
    notes end up unrecorded. Resolution order: the Planner that drove this plan,
    then the only Planner in the project, then nothing.
    """
    if plan:
        owner = owner_of_plan(plan, root)
        if owner:
            return owner
    planners = list_planners(root)
    return planners[0].name if len(planners) == 1 else ""


def link_plan(name: str, plan: str, root: str | Path = ".") -> Planner:
    planner = load(name, root)
    if plan not in planner.plans:
        planner.plans.append(plan)
        save(planner)
    return planner


def brief_lines(planner: Planner | None, plan: str = "") -> list[str]:
    """The 'what this Planner already knows' section of a briefing."""
    if planner is None:
        return []
    lines = [
        "## What you already know",
        "",
        f"You are **{planner.name}** ({planner.model}"
        + (f", effort {planner.effort}" if planner.effort else "")
        + f"). You have driven {len(planner.plans)} plan(s) in this project"
        + (f": {', '.join(planner.plans)}." if planner.plans else "."),
        "",
    ]
    if planner.notes:
        lines += ["Carried forward from those runs — treat as findings, not gospel:", ""]
        for note in planner.notes:
            where = f" [{note.plan}]" if note.plan else ""
            lines.append(f"- {note.text}{where}")
        lines += [
            "",
            "Verify anything that names a file, a flag, or a number before you rely",
            "on it — these were true when written, and the repository has moved since.",
        ]
    else:
        lines.append("No notes carried forward yet — this is your first run here.")
    lines += [
        "",
        "When you finish, record what the next run should not have to rediscover:",
        "",
        "```bash",
        invocation.cmd(f"planner note {planner.name}")
        + " "
        + (f"--plan {plan} " if plan else "")
        + '--add "..."',
        "```",
        "",
    ]
    return lines


def _first_existing(root: Path, *candidates: str) -> Path | None:
    for rel in candidates:
        path = root / rel
        if path.is_file():
            return path
    return None


def onboarding_lines(planner: Planner, root: str | Path = ".") -> list[str]:
    """A short block a person can paste into the session they just opened.

    A manual Planner is opened by hand, which means the harness cannot brief it
    the way it briefs a spawned one — and a person who has just run `create` has
    nothing to hand that session at all.

    Deliberately short, and mostly paths. The role contract and the ground rules
    are long, they are already written down, and pasting them into a prompt only
    means they can drift from the files that actually govern. Only files that
    exist are listed: sending a Planner to a missing path is worse than sending
    it nowhere, because it is told where to look and finds nothing.
    """
    root = Path(root).resolve()
    harness_dir = root / ".harness"
    base = harness_dir if harness_dir.is_dir() else root

    refs: list[tuple[Path, str]] = []
    # First, because it is the only one of these that knows what happened here
    # yesterday. The rest are standing documents; this one is the situation.
    handoff = _first_existing(root, "HANDOFF.md")
    if handoff:
        refs.append((handoff, "what the last session left you — read it first"))
    contract = _first_existing(base, "agents/planner.md") or _first_existing(
        root, "agents/planner.md"
    )
    if contract:
        refs.append((contract, "your role contract"))
    rules = _first_existing(root, "AGENTS.md")
    if rules:
        refs.append((rules, "ground rules for every agent here"))
    project = _first_existing(base, "configs/project.yaml") or _first_existing(
        root, "configs/project.yaml"
    )
    if project:
        refs.append((project, "what this project expects of you"))

    # Relative to the root, which line one already names: absolute paths here
    # are mostly noise, and the block has to stay short enough to paste.
    rels = [(str(path.relative_to(root)), why) for path, why in refs]
    width = max((len(rel) for rel, _ in rels), default=0)
    lines = [
        "─" * 72,
        f'You are the Planner "{planner.name}" for the project at {root}.',
        "",
        "Read these, in order:",
    ]
    lines += [f"  {rel:<{width}}  {why}" for rel, why in rels]
    lines += [
        "",
        "Then record what you are running on, so results can be compared:",
        f"  {invocation.cmd(f'planner set {planner.name} --model <the model you are>')}",
        "",
        "Then, from that directory:",
        f"  {invocation.cmd('status')}        # reads real state and names the next command",
        "",
        "The user will tell you what they want done. Agree what it is and what",
        "would count as done, then start the plan yourself — they do not run this:",
        f"  {invocation.cmd(f'plan new <name> --planner {planner.name}')}",
        "─" * 72,
    ]
    return lines
