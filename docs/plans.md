# Plans reference (Tier 1 ↔ Tier 2)

> New here? Start with the walkthrough at the top of [README.md](../README.md).

A **plan** is one piece of work — a series of module tasks — developed on its own
git branch in its own worktree. The Planner works there from start to finish; the
researcher reads the resulting report and decides whether to merge.

A plan is also the only unit of work here. There used to be a second concept
wrapped around it, called a *branch*, one-to-one with the plan and named after
the git feature underneath — three meanings for one thing. Now the plan is the
work, and its git branch and worktree are where that work happens.

The harness never merges. Which plan enters the record is the researcher's
judgement — it is the one thing here that is deliberately not automated.

```mermaid
flowchart TD
    R["Researcher (Tier 1)"] -->|"instruction"| E["harness plan new<br/>git branch + worktree"]
    E --> P["Planner = Main Worker (Tier 2)<br/>writes plans/&lt;name&gt;.yaml"]
    P -->|"materialize"| T["tasks/*.task.yaml"]
    T -->|"executor: main"| P
    T -->|"executor: sub — one at a time"| W["Sub-Workers"]
    W -->|"acceptance + deliverables"| T
    T -->|"all done"| I["integration spec"]
    I --> RPT["harness report"]
    RPT -->|"verdict + metrics"| R
    R -->|"researcher's call"| M["git merge &lt;name&gt;"]
```

## The goal

A plan states what it is for in its own `goal`, one paragraph, in the words you
would use out loud. There is no separate question to record and no form to fill
in: you and the Planner agree what the work is by talking, the Planner writes
that down as the goal, and the report answers to it.

Until the goal is written the plan is a scaffold, and `plan validate` refuses it
— so nothing gets built from a plan whose purpose nobody stated. The Planner's
briefing opens by saying exactly that, and names agreeing the work as its first
job.

**Starting a plan is the Planner's command, not the researcher's.** You say what
you want done; it agrees the shape with you and runs:

```bash
harness plan new <name> --planner <planner-name>
```

## Why worktrees

A worktree is a second working directory backed by the same repository. Several
plans can therefore exist on disk simultaneously, each on its own git branch,
without checking out and stashing between them.

Isolation is applied at the **plan** level, not the Worker level. Inside one
plan, Workers run **sequentially**. That is a deliberate choice:

- A plan's DAG is usually close to a chain (loader → model → train → eval), so
  concurrent Workers would save little wall-clock time.
- Concurrency would fracture the task board. Each Worker would edit its own
  copy of `tasks/`, so the Planner could not see progress and the
  dependency gate would read a stale board — refusing ready tasks and
  admitting unready ones.

Sequential Workers keep the board coherent, keep dependency gates correct, and
keep the board committed in git. If a genuinely wide plan ever needs
parallelism, `ready_task_ids()` already computes which modules could start
together; the door stays open.

## Commands

| Command | Purpose |
| --- | --- |
| `harness plan new <name> --planner <p> [--base main]` | Start plan `<name>`: git branch + worktree, scaffolded `plans/<name>.yaml` and its integration spec. The Planner runs this |
| `harness plans` (or `harness plan list`) | Every plan in flight, with its git branch and worktree |
| `harness plan validate\|approve\|materialize\|status\|run <name>` | The plan's own lifecycle — see [orchestration.md](orchestration.md). Each takes the plan's **name**, or a path to its YAML |
| `harness report <name> [--no-run] [--determinism] [--save]` | Build the researcher's decision aid; exits non-zero unless merge-ready |
| `harness plan drop <name> [--force]` | Remove the worktree; **the git branch is kept** — it is the record of the attempt |

Worktrees default to `.worktrees/<name>` inside the repo and are gitignored.
Removing a worktree never deletes the git branch, so a rejected plan remains
inspectable.

## Adopting an existing project

Most projects do not start empty. The order that works:

```bash
pip install research-harness                    # or: uv add research-harness
harness init .                                  # notices the code already here
harness create -n <planner-name>                # a Planner outlives this plan
harness project init                            # what it must know — it can write this
```

Then tell that Planner what you want done: it agrees the work with you and runs
`harness plan new` itself. Starting a plan is never something you have to do by
hand.

Under `uv add` every line above is `uv run harness ...`, and the harness prints
it that way: the prefix in its next steps, briefings and scaffolded contracts
follows how you invoked it.

`harness init` records how the harness arrived — the commit and how many source
files predate it — so "unverified" has a boundary instead of being a feeling.
Every Planner briefing then opens with that, until some plan here reaches a
report.

**The harness does not prescribe how to modularize an existing codebase, and
should not.** Deciding the decomposition is exactly the Planner's job; a fixed
pipeline in the tool would take Tier 2's work away and would be wrong for the
next project anyway. What the briefing supplies instead is what generalizes: the
conditions a module boundary has to satisfy — each one a consequence of
something the harness can or cannot enforce — and the ordering principle that in
research code the artifact of record is a *measurement*, so the numbers must be
pinned before anything moves. The Planner may disagree with any of it.

## Project context: what every Planner is told

A harness dropped into an existing project inherits a world it did not build.
Where the numbers of record live, which interpreter has the dependencies, which
directions are already closed — a Planner rediscovers all of it, differently
each time, and a Planner that reads the wrong document plans against the wrong
facts.

```bash
python -m harness project init    # scaffold configs/project.yaml, then edit it
python -m harness project show    # print it, and flag paths that do not exist
```

```yaml
project:
  docs:
    authority: docs/current_status.md      # wins when two sources disagree
    architecture: docs/current_architecture.md
  report_format: docs/current_status.md    # this project's own reporting shape
  environment: scripts/node_env.sh         # resolves per-machine paths
  python: /opt/envs/proj/bin/python        # steps get it as ${PROJECT_PYTHON}
  conventions:
    - "No t-tests on deterministic arms."
```

Every Planner briefing opens with this, and `harness plan new` copies it into the
new worktree. `project show` exits non-zero when a declared path is missing —
pointing a Planner at a moved file is worse than pointing it at nothing,
because it is told where the truth lives and finds none.

**`docs.authority` is the one that matters.** Point it at the detailed table,
not a summary. Summaries compress, and a compressed figure read as an
authoritative one is how a Planner reports a result nobody measured. This is
not hypothetical: a status document's summary line once read
`SMAD4 0.4282 -> 0.5483` — a single-branch figure (one arm of the study) — while the authoritative
per-task table said the arm scored `0.4465`.

**`${PROJECT_PYTHON}` is not `${HARNESS_PYTHON}`.** The latter is whatever
interpreter is running the harness, typically a bare `python3` with none of the
project's dependencies. Steps that need the project's packages must use
`${PROJECT_PYTHON}`.

## Planners that outlive one plan

A Planner spends its first hour learning the project. Discarding that at the
end of every plan means paying the hour again — and repeating the same
first-time mistakes, because what would have prevented them was never written
down.

```bash
python -m harness create -n icf --model claude-opus-5 --effort high
python -m harness plan new baseline --planner icf   # inherits model + memory
python -m harness planner note icf --plan baseline \
  --add "ICF_CKPT is empty on this node; that is correct for a training-free arm."
python -m harness planner show icf
```

A plan started under a registered Planner inherits its model (so it is never
"model not recorded") and opens its briefing with everything that Planner has
learned. The registry lives in the **main** repository, not in a worktree, so
every plan under one Planner appends to the same memory.

Notes are that Planner's operational findings, carried forward with an explicit
warning that they may have gone stale. Durable project policy belongs in
`project.yaml` instead, which the researcher owns — one is a lab notebook, the
other is the rules.

## Registering a Planner

The Planner is the session you are already talking to, so it is always opened by
hand — there is nothing to spawn. `harness create -n <name>` registers it once
per project and prints a short block to paste into that session; from then on
every plan it starts inherits its model and its notes.

One plan has exactly one Planner, so `harness plan new` records it: the plan's
briefing is printed directly, and the Planner is registered against it. There is
no separate activation step.

`planner brief` is then the *working* briefing — re-read current state at any
time:

```bash
python -m harness planner brief <name>
```

It always has the same sections — **The work**, **State**, **Next**, **Your
role**, **The whole sequence** — whatever the plan's state; only their
contents change. `Next` always names one real command, never the briefing
itself. A document that changes shape is one you have to re-read; this one you
re-run and skim.

**Tell the agent to run it; do not run it and paste the output.** Any coding
agent with shell access can execute it, and re-run it whenever it needs current
state. A pasted briefing is a snapshot that goes stale as soon as a Worker
finishes; the command always returns the board as it is now.

`--register` re-records the label (and `--model`/`--effort`) if the Planner
changes; `harness plan new` does it once for you.

This is a plain command producing plain text on purpose. Tool-specific shims
(a slash command, a skill, a saved prompt) are thin optional wrappers around it
— see [integrations/](../integrations/README.md). Binding registration to one
vendor's feature would make the template unusable for anyone with a different
tool.

## Running Workers

The Planner decides *which* task to run; the harness runs it. `task run`
invokes the configured Worker, verifies acceptance **and** deliverables, and
retries with the real failure output until the attempt cap:

```bash
python -m harness task run --id <id>     # one module
python -m harness plan run <plan>        # drain the ready queue in order
```

Retries keep the same worker and hand it the failing checks and step logs,
rather than starting over: a coding agent given its own failing test usually
fixes it, and continuing preserves context that a fresh start throws away. The
cap (default 6) exists so a wedged worker cannot burn budget forever — on
exhaustion the task is `blocked` with the reason in its log, and control
returns to the Planner.

Configure the adapter in `configs/worker.yaml`:

| Adapter | Behaviour |
| --- | --- |
| `manual` (default) | Write the briefing to a file and stop, for a human to hand to a session. Works with no configuration and no API key — and builds nothing. |
| `cli` | Run a configured shell command — a coding agent in headless mode. **This is what makes the Planner spawn Workers by itself.** The command is *your* configuration; the harness names no vendor. |

### Choosing the tier

```bash
harness setup --list
harness setup                       # interactive: both tiers
harness setup \
  --planner-platform <p> --planner-model <m> --planner-effort <level> \
  --worker-platform  <p> --worker-model  <m> --worker-effort  <level>
```

Both tiers are configured together, in `configs/agents.yaml`, so the split sits
in one file where you can see it. `--planner-session <id>` / `--worker-session
<id>` attach a tier to a session you already have open instead of starting a
fresh one.

A Worker's task is bounded and fully specified, so it can usually run on a
small fast model; keeping the expensive one for planning is the reason to
separate the tiers at all. **A tier you cannot choose is not a tier**, so the
platform, model, and reasoning level are explicit settings rather than
whatever a tool happens to default to. A command that references `{model}` or
`{effort}` without a value configured is refused, so a Worker never silently
runs at the platform default.

Presets live in `configs/agent-platforms.yaml` as **data** — adding a tool, or
a local model, is an entry there rather than a change to `harness/`. Nothing in
the harness core names a vendor.

Both tiers are recorded: `harness planner brief --register <label> --model <m>
--effort <e>` notes what the Planner session runs on (the harness cannot set
it — a person opened that session — but it can record it), and every Worker
invocation writes its platform, model, and effort to the task log and the
worker report. `harness report` shows both under **Tiers**, so which model built
what is auditable rather than merely intended.

Because the default is `manual`, automatic Workers are off until you turn them
on. `harness status` and `plan run` both say so rather than letting you wonder
why nothing was built. `configs/worker.yaml` carries worked examples for
several coding agents; check their flags against your installed version.

The briefing contains the task's brief, contract, deliverables, constraints,
and the exact acceptance commands that will judge the work. It reaches the
command two ways, so any tool fits: on **stdin**, and as a file via the
`{brief_file}` placeholder (`{task_id}`, `{task_file}`, and `{root}` are also
substituted).

A Worker edits files unattended, so it runs with permission prompts relaxed. A
plan's worktree is the right place for that: it is isolated, and its git branch
is disposable.

### On cost

The harness does **not** estimate token spend. It records what it can observe —
attempts, durations, exit codes, the configured adapter — and reports
`cost: not measured` when the adapter provides none. Guessing a number here
would be the same failure as an agent narrating an unmeasured result.

## The report

The report has two layers, and the split is the point.

**The spine is measured, not narrated.** Integration result, per-task
acceptance re-verification, determinism, the exact commit to merge, and a
`Not verified` section — all produced by the harness. An agent cannot assert
that its plan worked; the harness decides.

**The payload is what the researcher asked for.** The researcher states what
they want to see when instructing the Planner. The Planner records *where each
number lives*; the harness extracts the value from the artifact the run
actually produced. The Planner chooses the mapping and never supplies a value.

```yaml
plan:
  name: my-plan
  report:
    metrics:
      - name: val_accuracy
        source: ${HARNESS_RESULTS_DIR}/metrics.json   # where
        metric: val.accuracy                          # which key
    artifacts:
      - loss_curve.png
```

### Self-containment is enforced

A report may only draw on **its own** plan's artifacts. `source` and
`artifacts` paths may not be absolute and may not escape via `..`;
`plan validate` rejects them:

```
error: report metric 'acc' source must stay inside the plan:
'../baseline/results/stats.json' escapes via '..'.
Comparing plans is the researcher's job, not the plan's.
```

Comparing plans belongs to Tier 1: the researcher collects finished reports and
weighs them. A plan that reaches into another cannot be judged on its own terms,
so the harness refuses to produce one. Every report is
also written as `report.json`, which makes that collection straightforward.

### Merge readiness

`harness report` exits `0` only when integration passed, every module is `done`,
every task's acceptance still passes, the worktree is clean, and determinism
(if checked) held. Anything short of that prints `NOT READY` and exits `1`,
and **every** blocker is stated under `Why not ready` — a verdict the
researcher cannot explain is not a decision aid.

Progress is counted against the plan's own modules. A `tasks/` directory can
hold task files from other plans; counting those would report a plan complete
when none of its modules were built, and that number decides a merge. Foreign
task files are ignored and listed as a caveat.

`--save` also writes the report to `plans/<name>/report.md` inside the plan's
worktree, so merging carries the evidence into the record. Without it the report
lives only under `results/` and is gitignored.

## Typical flow

```bash
# Researcher: register the Planner, then talk to it
python -m harness create -n my-planner

# Planner: once you agree what the work is, start the plan
python -m harness plan new sparse-attn --planner my-planner

# Planner, inside .worktrees/sparse-attn/
# (plan new scaffolds plans/<name>.yaml and configs/<name>.yaml; fill in the
#  TODOs first — plan validate refuses a scaffold)
python -m harness plan validate sparse-attn

# Researcher approves what the Planner explained
python -m harness plan approve sparse-attn --by <researcher>

# Planner materializes and runs it; Workers go one at a time
python -m harness plan materialize sparse-attn
python -m harness plan run sparse-attn

# Planner closes the loop, then hands back
python -m harness report sparse-attn --determinism --save

# Researcher decides
git merge sparse-attn
```

## Code in a worktree

The runner puts the tree being verified at the front of `PYTHONPATH` (its root
and `src/`), so a step run inside a plan's worktree imports **that** plan's
code. Without it an editable install would point every worktree at the main
checkout, and plans would silently verify the wrong source.
