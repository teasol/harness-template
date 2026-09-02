# Research Harness

An agent-first harness for research code. It does three things, and it is worth
being precise about which:

| | | |
|---|---|---|
| **1** | **Mechanical verification** | Runs the project's own tests as a named checklist, gates every module's "done" on them, and records what produced each run. Judges pass/fail on evidence, never on opinion. |
| **2** | **Two-tier orchestration** | A Planner that is also the Main Worker decomposes the work into modules with contracts, builds them, and hands the routine bulk to Sub-Workers one at a time. |
| **3** | **Handoff** | One document, always current, that a session which was not here reads instead of being told — so the work resumes on another day, another machine, another tool. |

Python 3.10+, standard library plus PyYAML. No API key needed to start: the
default agent adapter writes a briefing and stops for a human to carry.

```bash
pip install git+https://github.com/teasol/harness-template   # or: uv add git+…
harness init .            # scaffold into an existing project
harness create -n <planner-name>
```

Then talk to that Planner. Everything after registration is its move, including
starting the plan.

---

## 1. Mechanical verification

This is the layer everything else rests on, so it is deliberately small and
hard to talk round. It verifies **three** things, all of which reduce to exit
codes and files on disk.

### What it actually checks

**A named test ran and exited zero.** The harness ships no assertions of its
own. It manages a **checklist**: each item is addressed as `<module>:<name>`
(`loader:parses-v2`) and names a command the project already has — pytest, a
shell script, a CLI of its own — and the verdict is that command's **exit
code**, which is the one thing that means the same in every project. An item
has an optional `timeout` so that hung and failed are different outcomes, and
every command is handed a fixed environment — `HARNESS_RESULTS_DIR` (this
run's artifact directory), `HARNESS_RUN_ID`, `HARNESS_PYTHON` (the interpreter
to use), `HARNESS_SEED`, `HARNESS_DIR` — so an item never hardcodes a path and
sees the same contract wherever it runs.

Writing the test is the project's job, because only the project knows what
would count as working. "Does the code work" is not the same question twice —
a log parser and a training loop need different tests, and the four built-in
check types and spec language this layer once shipped fit neither. Knowing
which tests exist, which module each belongs to, and which of them pass right
now is this layer's.

**Every declared deliverable exists.** This is a separate gate from the
checklist. A task lists the files it must produce under `deliverables:`, and
the harness synthesizes an item asserting each one — so a task **fails even
when every checklist item passed** if a declared file is missing. "The tests
passed but nothing was written" cannot be reported as done. The corollary
matters: an incomplete `deliverables` list is an unenforced contract.

**What produced a run is recorded.** Commit, branch, interpreter, platform,
harness version — a report is unreadable without it, so the harness collects
it around every run. What counts as *reproducible* is not recorded, because
that differs by project: a project that needs a determinism gate writes it as
a checklist item of its own, rather than carrying one built for no project in
particular.

```bash
harness check --plan <name>              # every item in the plan
harness check <module> --plan <name>     # one module's items
harness check <module>:<item> --plan <name>
harness checklist --plan <name>          # print every item and its command
```

### Two levels

- **Per module** — a task's `checklist`: did this one module meet its contract?
  `harness task verify --id <id>`
- **The whole** — the plan's own `checklist`: does the assembled thing work?
  Required; a plan without one fails `plan validate` rather than surviving to a
  report that could never approve it. Modules that each pass alone can still
  not add up.

`harness task verify --all --status done` re-verifies everything that *claims*
to be done. CI runs exactly that, so a task that passed once and broke later is
caught rather than believed.

### What makes the verdict trustworthy

A verdict only means something if the thing issuing it was not tampered with.

**The harness hashes itself around every Worker invocation.** If the package
changed during an attempt, the task fails outright. This was not hypothetical: a
Worker once patched the harness mid-attempt, and **acceptance under a rewritten
harness proves nothing**. Under the default `guard: strict` a task also fails if
the Worker modified a tracked file it never declared as a deliverable.

**The tree under test comes first on `PYTHONPATH`.** An editable install points
at one checkout, so without this a step running inside a plan's worktree would
import the main checkout's code — and the plan would be verifying somebody
else's source.

### What it does not verify

**Whether the code is good, or the approach right.** That is the user's and the
Planner's judgement. The harness answers pass or fail, and nothing else.

**Whether a research claim is true.** Its guarantees are about code and the
artifacts code produces. Re-running a full training run to check a conclusion is
hours of GPU time per repetition, which is not the harness's to spend — this is
why the human role is called the *user*, not the researcher.

One consequence worth naming: **the numbers in a report are extracted by the
harness, not written by the Planner.** A plan's `report:` block declares *where*
each number lives; the harness reads it out of the real artifact. So a Planner
cannot quote a figure it was not made to measure.

---

## 2. Two-tier orchestration

**Tier 1 — the user and the Planner.** You agree what the work is, what would
count as done, and what gets reported. You approve the plan and you decide what
gets merged. The Planner never merges.

**Tier 2 — the Planner, which is also the Main Worker.** It writes the plan,
then **builds the modules itself**: that is the default and the normal case. A
module is handed to a **Sub-Worker** only where the plan says so — routine bulk
a brief can specify completely, long mechanical coding, log parsing. One
Sub-Worker at a time, one module each, then control returns.

### A plan is the unit of work

One plan is one piece of work, on its own git branch in its own git *worktree*
under `.worktrees/<name>`, from first sketch to final report. Plans are
addressed by name.

```bash
harness plan new <plan-name> --planner <planner-name>   # the Planner runs this
harness plan validate <plan-name>                       # schema, DAG, deliverables
harness plan approve <plan-name> --by <you>             # the user runs this
harness plan materialize <plan-name>                    # plan → task files
harness plan run <plan-name>                            # work through it
harness report <plan-name> --determinism --save         # the decision aid
```

A plan nobody agreed to is a proposal, and `plan run` refuses to start on one.
The Planner does not approve its own plan: the point is that a second party saw
it before the money was spent, and the report records who.

### Who builds each module

```yaml
modules:
  - id: loader
    executor: main        # the default — the Planner builds it
  - id: bulk-ingest
    executor: sub         # a Sub-Worker is spawned for this one, and hands back
```

`executor` defaults to `main`, so nothing is delegated behind the Planner's
back. Either way the module keeps its contract and its checklist: what changes
is who writes the code, never whether it is verified.

`plan run` is the Main Worker's loop. It walks the plan in dependency order,
spawns a Sub-Worker where the plan says to, and **stops the moment the next
module is the Planner's** — naming that module and the commands that hand it
back. Nothing later in the plan is started ahead of it.

If a delegated task exhausts its attempts the harness blocks it and hands it
back. That leaves two honest options: fix the brief, or set `executor: main`,
re-materialize with `--force`, and build it. Taking a module over costs no
verification — it keeps its contract either way.

---

## 3. Handoff

Sessions end for reasons that have nothing to do with the work: the context
window fills, the laptop closes, tomorrow is the other machine with a different
tool. Each of those starts a session that knows nothing, and re-explaining a
project by hand is expensive and lossy.

So the harness keeps one document current. Handing over is handing over a path.

```bash
harness handoff        # writes HANDOFF.md, prints where
```

`HANDOFF.md` sits at the root of the main working tree and is **regenerated
whenever the work moves** — `task done`, `task block`, `plan approve`,
`plan new`, `plan drop`, every pass of `plan run`. Half of it is derived from
the plan and task files, so it cannot go stale and nobody maintains it: which
plan is in flight, which module is next, what is blocked, the command to run.

The other half is what no file records, and it is the half worth reading:

```bash
harness note "chose single-process: mp broke the seed" --decision
harness note "the loader only reads v2 fixtures" --dead-end
harness handoff --next "mid-way through the widget acceptance"
```

One line each, recorded **as it happens** rather than saved for the end of a
run — runs do not end tidily. A dead end nobody wrote down is a session somebody
spends again. `harness note` infers who is speaking and which plan it belongs
to, because a session just handed a document does not know its own registered
name.

Commit `HANDOFF.md` and `.harness/`: a handoff that only exists on the machine
that wrote it hands nothing over.

### Picking the work up elsewhere

A worktree is local; a branch is not. After `git pull` on the second machine the
plan is *there*, on its branch, with no working copy — and `plan new` refuses
the name because the branch exists.

```bash
harness status                       # names the plans that are here on a branch
harness plan resume <plan-name>      # same branch, same history, nothing scaffolded over it
```

Before this existed, that clone looked exactly like a project with no work at
all, and `status` offered to start a new plan — the one answer that loses the
work.

---

## Commands

```bash
harness status                  # where am I, what next (start here)
harness init <dir>              # scaffold a project
harness create -n <name>        # register a Planner — the one thing you run
harness setup [--check]         # choose the Sub-Worker platform / model / effort
harness progress --watch        # what is running right now (second terminal)

harness check <module>[:<item>]  # run checklist items and judge them
harness checklist --plan <name>  # print every item and the command behind it

harness plan new|validate|approve|materialize|run|status|check|drop|resume
harness plans                   # every plan in flight
harness task list|show|claim|verify|done|block|run
harness report <plan-name>      # exits non-zero until merge-ready

harness handoff [--next "…"|--show]
harness note "…" [--decision|--dead-end]
harness planner create|list|show|note|brief|set
harness project                 # what a Planner must know about this project
```

Every command works as `harness …`, `python -m harness …`, or
`uv run harness …`, and the harness prints back whichever form you used.

## What a project gets

`harness init` puts everything the harness owns under `.harness/`, so it cannot
collide with what is already there:

```
<my-project>/
├── AGENTS.md               # ground rules every agent reads (root, by convention)
├── HANDOFF.md              # what the last session left the next one (generated; commit it)
├── .gitignore              # harness entries appended to whatever was there
├── .worktrees/             # one worktree per plan in flight
├── results/                # run artifacts and reports
└── .harness/
    ├── agents/planner.md   #   Planner = Main Worker: plans, and builds
    ├── agents/worker.md    #   Sub-Worker: one module task, in isolation
    ├── configs/agents.yaml #   which agent runs which tier
    ├── plans/ tasks/       #   plans and materialized work orders
    ├── scripts/demo_step.py#   example command for a first checklist item
    ├── planners/           #   registered Planners and what they have learned
    └── adoption.json       #   how the harness arrived, if code predated it
```

Adopting an existing project is the normal case: `init` notices code it did not
build, records the commit it arrived at, and every Planner briefing opens with
that situation until some plan has covered it.

## This repository

The package, not a project using it — there is no `.harness/` here and the
harness is not run on itself.

```
harness/
├── cli.py           the one grammar, and everything it prints
├── paths.py         layout resolution
├── invocation.py    how the harness was invoked, so printed commands paste
├── project.py       the project manifest both the engine and the Planner read
├── init.py          scaffolding
├── verify/          checklist · provenance · report · heartbeat   — role 1
├── orchestrate/     plan · task · plans · worker · guard · setup   — role 2
└── handoff/         document · planners · adoption   — role 3
templates/           the files `harness init` copies into a project
tests/               258 tests, no network, no API key, no model calls
```

`verify/` knows nothing about Planners, plans or tasks: remove the layers above
and it still runs. `orchestrate/` decides who does what and in what order;
whether the result is acceptable is `verify/`'s answer, never its own.

```bash
make test      # pytest
make lint      # ruff check + format check
make check     # the demo plan's checklist, end to end
make build     # wheel, and a listing of exactly what it ships
```

Two things to know before changing anything here. Everything a project receives
lives in `templates/` — a user installs the package and never has this
checkout, so a contract or a config changes *there*. And the `Verification`
workflow runs on pull requests, manual dispatch and weekly, **not** on a push
to `main`, which is how a broken step once survived two releases: if you touch
what it exercises, run its steps locally.

## License

MIT — see [LICENSE](LICENSE).
