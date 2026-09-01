# Harness Template

An agent-first harness for **work you need to be able to trust**: a Planner you
talk to, Sub-Workers it delegates to, and machine-checked verification of
whatever comes out of either.

## Getting started

You and the Planner talk about what you want done. It writes a plan, explains
it, and — once you agree — **builds it itself**, module by module, handing a
Sub-Worker the occasional chunk that is routine bulk. Each plan lives on its own
git branch. When it is finished you get a report — did integration pass, which
modules were built, does it reproduce, and the numbers you asked for — and
**you** decide whether to merge. The harness measures; it never decides.

Lost at any point:

```bash
python -m harness status     # reads the real state, names the next command
```

### 0. Quickstart

```bash
# 1. Install:
pip install git+https://github.com/teasol/harness-template.git

# 2. Initialize in your project:
cd <my-project>
harness init                                   # scaffolds AGENTS.md, agents/, configs/

# 3. Create the Planner you will work with:
harness create -n <planner-name>               # --model / --effort optional
```

**Using uv?** Add the harness to the project you are about to initialize (which
needs a `pyproject.toml` — run `uv init` first if it has none), and run every
harness command through `uv run`:

```bash
cd <my-project>
uv add git+https://github.com/teasol/harness-template.git
uv run harness init                            # scaffolds AGENTS.md, agents/, configs/
uv run harness create -n <planner-name>        # --model / --effort optional
```

Every `python -m harness ...` below is then `uv run harness ...` — same
commands, resolved against the project's environment. **You do not have to
translate them yourself:** every command the harness prints — init's next
steps, the Planner's briefing, the block you paste into a Planner session, the
scaffolded `AGENTS.md` — carries the prefix that matches how you invoked it. For
a harness you want available outside any one project, `uv tool install
git+https://github.com/teasol/harness-template.git` puts `harness` on your PATH
instead.

`harness init` scaffolds the role contracts (`agents/`), platform configuration
(`configs/`), and asks which agent runs your **Sub-Workers**. The Planner is not
configured, because the Planner is the session you are already talking to.

### 1. Create a Planner, and let it start the plan

**The Planner comes first, and it outlives any one plan.** Registering one is
the only step here that is always yours to run.

```bash
python -m harness create -n <planner-name>      # --model / --effort optional
```

`--model` is optional: it defaults to whatever the Planner tier records, and a
Planner you drive by hand legitimately has none until its session says what it
is (`harness planner set <planner-name> --model <model>`). Until then every
report under it notes that the run cannot be compared with another.

You then talk to that Planner, and *it* starts the plan once you agree what the
work is:

```bash
python -m harness plan new <plan-name> --planner <planner-name>   # the Planner runs this
```

Starting a plan creates the git branch, an isolated working directory (a git
*worktree*) under `.worktrees/`, a plan skeleton, and prints the briefing:

```text
Plan '<plan-name>' created, worktree at <my-project>/.worktrees/<plan-name>.
Its Planner is '<planner-name>'. Everything below is that Planner's briefing —
follow it, or hand this session to whoever will.

======================================================================
# Planner briefing: <plan-name>

## The work

No goal written yet. Work out with the user what this plan is for:
what they want done, what would count as done, what they want to see at
the end. Then write it as the plan's `goal` — one paragraph, in the
words you would use out loud — and explain the plan before building it.
...
```

**There is no question to record and no form to fill in.** You talk; the Planner
writes what you agreed as the plan's `goal`, and the report answers to that.

**Adopting an existing project?** That is the normal case, and `harness init`
notices: it records how the harness arrived — the commit, and how many source
files predate it, so "not verified yet" has a boundary instead of being a
feeling — and prints the order that works, which is the same order, plus one
step:

```bash
python -m harness create -n <planner-name>      # the Planner you will talk to
python -m harness project init                  # what it must know here — it can write this
```

Writing down what the project expects is work the Planner can do itself once it
is open and reading its own briefing, so it does not hold up the conversation.

That first briefing opens with the situation: none of this code is covered by a
contract or an acceptance check yet, and making it verifiable is its first job.
**The harness does not prescribe how to modularize an existing codebase** —
deciding the decomposition is the Planner's work, and a pipeline baked into the
tool would be wrong for the next project anyway. See
[docs/plans.md](docs/plans.md#adopting-an-existing-project).

### 2. Stay oriented

```bash
python -m harness planner brief <plan-name>
```

The same briefing, re-read from current state. It always has the same sections
— **The work**, **State**, **Next**, **Your role** — so you skim it rather than
re-read it, and `Next` always names the one command to run now.

### 3. Agree the plan, then let it be built

The Planner drafts a plan, validates it, and **explains it to you**: what it
will establish, each module and who builds it, why this decomposition rather
than the obvious alternative, what it will cost, and what would make it fail.

```bash
python -m harness plan validate <plan-name>
python -m harness plan approve <plan-name> --by <you>   # you run this
python -m harness plan materialize <plan-name>
python -m harness plan run <plan-name>
```

**A plan is a proposal until you approve it, and `plan run` refuses to start on
one that nobody has.** Approving prints what it commits you to — the module list
and the worst-case agent time, which is otherwise invisible: two modules at six
attempts and a 30-minute cap is a six-hour ceiling. Approval is fingerprinted
against the plan's contents, so editing it lapses the approval. If the Planner
approves its own plan the report says so, because the point of the gate is that
somebody else saw it.

**The Planner builds the modules. Delegation is per-module and opt-in:**

```yaml
executor: main   # the default — the Planner does it
executor: sub    # this one goes to a Sub-Worker — routine bulk a brief can fully specify
```

`plan run` is the Main Worker's own loop: it walks the modules in dependency
order, spawns a Sub-Worker for each one marked `sub`, and **stops as soon as the
next module is the Planner's** — saying which module, and how to hand it back:

```text
1 task(s) delegated this pass; 1/3 module(s) done

module '<module-id>' (2/3) is yours to build — nothing was spawned for it.
Build it, then hand it back to the loop:

  python -m harness task show --id <module-id>            # its brief, contract and acceptance
  python -m harness task verify --id <module-id>          # when you think it is done
  python -m harness task done --id <module-id> --by planner
  python -m harness plan run <plan-name>                  # continues from here
```

For a delegated module the harness checks acceptance **and** declared
deliverables, and retries with the real failure output. It stops early when it
stops making progress: three attempts in a row that change no deliverable hand
back to the Planner rather than spend the rest of the cap repeating one failure.
A module that fails is `blocked`, and the Planner has two real moves — fix the
brief, or **take it back** (`executor: main`, re-materialize with `--force`,
build it itself). Either way the module keeps its contract and its acceptance,
so taking it back costs no verification.

**Watching a long run.** Steps and Sub-Worker attempts buffer their output until
they exit, so from a second terminal:

```bash
python -m harness progress --watch
```

```text
worker <module-id> (module 1/2 · attempt 2/6) · running 12m30s · 17m30s before the cap
```

A heartbeat that stopped ticking is reported as **dead**, not slow — the
distinction you actually need during a long wait.

### 4. Read the report and decide

```bash
python -m harness report <plan-name> --determinism --save
```

```text
[<plan-name>] READY TO MERGE
  integration: PASSED
  tasks:       2/2 done
  determinism: REPRODUCIBLE
  commit:      1ebd0bed...
  retained_mass: 0.198128
```

**`READY TO MERGE` means the harness could not find anything wrong — not that
the result is any good.** That judgement is yours. `NOT READY` lists exactly
what is missing.

Then, if you want it:

```bash
git merge <plan-name>
```

Nothing merges on your behalf. Decide against a plan and simply don't merge —
its git branch remains, so the attempt stays on the record.

### Running several at once

```bash
python -m harness plan new <plan-name> --planner <planner-name>
python -m harness plan new <other-plan> --planner <planner-name>
python -m harness plans
```

One Planner can own several plans — that is why it is registered separately from
any of them. Each report stands on its own: a plan may not read another's
results, and the harness rejects a plan that tries. Comparing them is **your**
job, done by reading the finished reports.

### When the session changes — and it will

Sessions end for reasons that have nothing to do with the work: the context
window fills, the laptop closes, tomorrow is spent on the other machine with a
different tool. Every one of those starts a session that knows nothing, and
re-explaining a project by hand is both expensive and lossy.

So the harness keeps one document current, and handing over is handing over a
path:

```bash
python -m harness handoff        # writes HANDOFF.md, prints where
```

`HANDOFF.md` sits at the project root and is **regenerated whenever the work
moves** — a task finishes, a plan is approved, `plan run` hands back. Half of it
is derived from the plan and task files, so it cannot go stale: which plan is in
flight, which module is next, what is blocked, what command to run. The other
half is what no file records, and it is the half worth reading:

```bash
python -m harness note "chose single-process: mp broke the seed" --decision
python -m harness note "the loader only reads v2 fixtures" --dead-end
python -m harness handoff --next "mid-way through the widget acceptance"
```

One line each, recorded *as it happens* rather than saved for the end of a run —
runs do not end tidily. A dead end nobody wrote down is a session somebody
spends again.

Commit `HANDOFF.md` and `.harness/`: a handoff that only exists on the machine
that wrote it hands nothing over. Then on the other machine:

```bash
git pull
python -m harness status               # names the plans that are here on a branch
python -m harness plan resume <plan-name>
```

A plan lives on its own git branch in its own worktree, and a worktree is
local — so a fresh clone has the branch and not the working copy. `plan resume`
gives it one back: same branch, same history, nothing scaffolded over it. Before
this existed, that clone looked exactly like a project with no work at all.

### When something goes wrong

| Symptom | What it means |
| --- | --- |
| The briefing says **no goal written yet** | Talk it through, then write it as the plan's `goal`. |
| `plan validate` says "still the scaffold" | The TODOs have not been filled in yet. |
| `plan run` says **this plan has never been approved** | Read it, then `harness plan approve <plan-name> --by <you>`. Editing a plan lapses its approval. |
| A task is `blocked` | A Sub-Worker used up its attempts. Fix the brief, or take the module over with `executor: main`. |
| A task stopped after **3 attempts changed no deliverable** | The Sub-Worker is wedged, not slow. The brief or the acceptance is wrong. |
| A Sub-Worker exited in **under 5 seconds** | A misconfigured command, not a coding problem. `harness setup --check`. |
| A task aborted: **the Worker changed the harness** | Containment did its job. Acceptance under a rewritten harness proves nothing. |
| `report` says `NOT READY` | It lists every blocker. Fix them — or decide the plan failed, which is a valid outcome. |
| `NOT REPRODUCIBLE` | Something is unseeded. See [docs/reproducibility.md](docs/reproducibility.md). |
| A long run looks hung | `harness progress --watch` from another terminal. |
| A fresh clone says **no plans yet** | The plan's branch is there, its worktree is not. `harness status` names it; `harness plan resume <plan-name>`. |
| `HANDOFF.md` is thin | Only derived state was recorded. Decisions and dead ends are recorded by hand, one line at a time — see above. |
| `cost: not measured` | Expected. The harness does not estimate token spend. |
## Why

Work rots because verification is ad-hoc: "run this, eyeball the output." This
template makes verification a first-class artifact, then builds an agent
workflow on top of it:

- **Declarative** — what to run and what to check lives in YAML, not tribal memory.
- **Deterministic** — seeds are explicit; every run's artifacts are hash-compared.
  Declaring a seed does not silently change your numbers.
- **Enforced** — anything declared (deliverables, dependencies, report sources)
  is machine-checked. A rule the harness cannot check is prose, and prose is
  not a gate.
- **Measured, not narrated** — an agent says *where* a number lives; the
  harness reads the artifact. No result reaches you on an agent's word.
- **Contained** — a Sub-Worker that edits the harness, or a tracked file it never
  declared, fails the task. Acceptance run under a harness the agent just
  rewrote proves nothing, so this is enforced rather than asked.
- **Agreed before it is built** — a plan nobody approved cannot run.
- **Human at the end** — the harness reports readiness and stops. What gets
  merged is your decision, and the one thing here that is deliberately not
  automated.

## How it works

**Two tiers.** You and the Planner settle what the work is and you decide what
gets merged. Everything else happens inside one plan, where the Planner is
also the **Main Worker**: it implements the core work itself and delegates
routine bulk to a **Sub-Worker**, one at a time. One Planner runs many plans
over the life of a project.

```mermaid
flowchart TD
    subgraph T1 ["Tier 1 · what the work is, and what gets merged"]
        direction LR
        U(["👤 You"]) <-->|"dialogue"| P["🧠 Planner"]
    end

    subgraph T2 ["Tier 2 · serial execution"]
        direction TB
        subgraph BR ["🌿 one plan — &lt;plan-name&gt;"]
            direction TB
            MW["🤖 Main Worker — the Planner itself<br/>plans, and builds the modules"]
            subgraph EX ["serial execution, module by module"]
                direction TB
                SELF["executor: main — the default<br/>the Planner implements it"]
                SUB["⚙️ Sub-Worker<br/>executor: sub — the bulk it handed off"]
            end
            MW ==>|"builds most modules itself"| SELF
            MW -->|"spawns for one module"| SUB
            SUB -->|"done — control returns"| MW
        end
        H["⚙️ Harness<br/>integration · module acceptance<br/>reproducibility · metric extraction"]
    end

    P ==>|"starts each plan"| MW
    EX ==>|"completed run"| H
    H ==>|"report — metrics and status"| P
    P ==>|"report"| U
    U --> DEC{"merge?"}
    DEC -->|"yes"| MAIN[("🌿 main")]
    DEC -->|"no"| NEXT["next plan · iterate"]
    NEXT --> P
```

The harness verifies whatever comes out, whoever produced it: a module the
Planner wrote itself carries the same contract and the same acceptance as one a
Sub-Worker built. What the split changes is who writes the code, never whether
it is checked.

### Tier 1 — plans and the merge decision

```bash
python -m harness create -n <planner-name>                        # a Planner, once
python -m harness plan new <plan-name> --planner <planner-name>   # git branch + worktree
python -m harness planner brief <plan-name>                       # current state, any time
python -m harness plans                                           # what is in flight
python -m harness report <plan-name> --determinism --save
git merge <plan-name>                                             # only you do this
```

A Planner made with `harness create` outlives one plan: it carries its model —
so a report is never "model not recorded" — and the notes it wrote in earlier
runs, which is the hour it spent learning the project not being paid twice.
`harness planner note <planner-name> --add "..."` records something the next run should
not have to rediscover. Durable project policy belongs in
`configs/project.yaml` instead, which you own: one is a lab notebook, the other
is the rules.

`report` measures the spine itself — integration result, per-task acceptance
re-verified, determinism, the exact commit to merge, and an explicit list of
what went **unverified** — then extracts the metrics you asked for from real run
artifacts.

Full reference: [docs/plans.md](docs/plans.md).

### Tier 2 — modules, and the ones you hand off

```mermaid
flowchart TD
    P["Planner"] -->|"writes"| PL["plans/*.yaml<br/>goal · DAG · contracts · report"]
    PL -->|"you approve"| A["plan approve"]
    A -->|"materialize"| T["tasks/*.task.yaml<br/>self-contained work orders"]
    T -->|"executor: main — the default"| P
    T -->|"executor: sub — opt in per module"| W["Sub-Workers"]
    W -->|"implement"| S["src/… deliverables"]
    P -->|"implements"| S
    S -->|"acceptance + deliverables"| H["Runner + checks"]
    H -->|"pass → done · fail → retry · exhausted → blocked"| T
    T -->|"all done"| I["integration spec"]
```

`executor` defaults to `main`, so the arrow that matters here is
`T -->|executor: main| P`: the Planner is the one building, and a Sub-Worker is
what it reaches for when a module is bulk. Sub-Workers run **one at a time**
within a plan, and only while the Main Worker waits for that one module.
Isolation belongs at the plan level: a plan's DAG is near-linear so concurrency
buys little, while per-Worker branches would fracture the task board and make
dependency gates read stale state.

- **Planner / Main Worker contract**: [agents/planner.md](agents/planner.md)
- **Sub-Worker contract**: [agents/worker.md](agents/worker.md)
- **Full reference**: [docs/orchestration.md](docs/orchestration.md)

### Choosing the Sub-Worker

You only need this configured for the modules you delegate; a Planner that
builds everything itself never spawns one.

```bash
python -m harness setup --list      # platforms available
python -m harness setup --check     # can it actually be driven?
python -m harness setup             # interactive, or:
python -m harness setup \
  --worker-platform opencode --worker-model deepseek/deepseek-v4-flash --worker-effort low
```

Only the Sub-Worker tier is configured. The Planner is the session you are
talking to — always manual, never spawned — so there is nothing to choose for
it. A Sub-Worker's task is bounded and fully specified, so a small fast model
usually suffices; that difference is the point of splitting the two.

Platforms that list models offer them by number rather than asking you to type
an exact id, because a wrong id does not fail at setup — it fails much later as
an agent that never runs. Free text is still accepted.

`setup --check` sends one cheap prompt and reports whether the agent actually
received it. A preset that does not match the installed CLI otherwise fails
silently: attempts ending in under a second each, with the agent never having
seen the task.

Platform knowledge lives in `configs/agent-platforms.yaml` as **data** — adding
your lab's tool, or a local model, is an entry there, not a change to
`harness/`.

## The verification layer

Everything above stands on one engine: a spec is an ordered list of steps, each
a shell command followed by checks.

```mermaid
flowchart LR
    A["Spec YAML"] -->|"load_spec"| B["Runner"]
    B -->|"subprocess"| C["Steps"]
    C --> D["Artifacts<br/>results/runs/…"]
    D -->|"run_check"| E["Checks"]
    E --> F["report.json / report.md<br/>+ provenance"]
    F --> G["CI gate"]
```

```yaml
name: my-check
seed: 42                    # exports PYTHONHASHSEED; does NOT change your numbers
deterministic_math: false   # opt in to CUBLAS_WORKSPACE_CONFIG — this DOES change them
steps:
  - id: train
    run: ${HARNESS_PYTHON} scripts/train.py --seed ${HARNESS_SEED}
    timeout: 3600
    checks:
      - type: file_exists
        path: ${HARNESS_RESULTS_DIR}/metrics.json
      - type: json_metric
        path: ${HARNESS_RESULTS_DIR}/metrics.json
        metric: val.accuracy
        min: 0.5
```

A task's acceptance is a spec run by this same Runner, and a plan's report is
that machinery run over a whole plan. One engine, three altitudes.

Every report records its provenance — commit, dirty flag, interpreter, platform,
seed, and every environment variable the harness injected — so a result found
months later can be traced back to code. `seed:` seeds and nothing more:
deterministic GPU math constrains kernel selection and therefore changes
results, so it is opt-in via `deterministic_math` rather than something a seed
turns on behind your back.

`harness reproduce` runs a spec twice and diffs a hash manifest of **every**
artifact, and refuses to pass a spec that produced nothing to compare.

Full reference: [docs/verification.md](docs/verification.md) ·
[docs/reproducibility.md](docs/reproducibility.md).

## Everyday commands

```bash
make status        # where am I, what next
make verify        # run the verification spec end-to-end
make reproduce     # run it twice and diff every artifact (determinism gate)
make run           # put every ready task through a Sub-Worker
make test          # pytest suite
make lint          # ruff check + format check
make audit         # re-verify every task marked done
make drift         # validate every plan, fail on task/plan drift
make plans         # list plans in flight

python -m harness progress --watch   # what is running right now (second terminal)
python -m harness handoff            # refresh HANDOFF.md for whoever comes next
python -m harness note "..." --decision | --dead-end
```

## Project structure

```
harness-template/
├── AGENTS.md               # Agent-facing ground rules (read this first)
├── HANDOFF.md              # What the last session left the next one (generated, commit it)
├── Makefile                # status / setup / verify / reproduce / run / audit / drift
├── pyproject.toml          # Project metadata + tool config
├── .worktrees/             # One worktree per plan in flight (gitignored)
├── .harness/               # Harness state that is not project code
│   ├── configs/agents.yaml #   the Sub-Worker tier (the Planner is you)
│   ├── configs/project.yaml#   authoritative docs, report format, conventions
│   ├── planners/           #   registered Planners and what they have learned
│   └── adoption.json       #   how the harness arrived, if code predated it
├── agents/                 # Role contracts
│   ├── planner.md          #   Planner = Main Worker: plans, and implements
│   └── worker.md           #   Sub-Worker: one module task, in isolation
├── plans/                  # Plans (Planner output)
├── tasks/                  # Materialized work orders + lifecycle
├── harness/                # The harness itself (stdlib + PyYAML only)
│   ├── spec.py             #   Spec loading & validation
│   ├── runner.py           #   Step execution engine
│   ├── checks.py           #   Built-in checks + registry
│   ├── report.py           #   JSON/Markdown report generation
│   ├── reproduce.py        #   Determinism gate: repeat a spec, diff artifacts
│   ├── reproducibility.py  #   Seeding, hashing, run provenance
│   ├── plan.py             #   Plans: module DAGs, contracts, approval
│   ├── task.py             #   Task lifecycle, board, deliverable enforcement
│   ├── worker.py           #   Sub-Worker adapters + the verify-and-retry loop
│   ├── guard.py            #   Containment: the harness is not the agent's to edit
│   ├── heartbeat.py        #   Where a run is right now, from another terminal
│   ├── plans.py            #   Plans in flight: worktrees, briefings, reports
│   ├── project.py          #   What a Planner must know about THIS project
│   ├── planners.py         #   Planners that outlive one plan, and their notes
│   ├── adoption.py         #   Landing on a codebase that already exists
│   ├── setup.py            #   Choosing the Sub-Worker platform / model / effort
│   └── cli.py              #   status | progress | setup | verify | plan | task | report
├── src/                    # Your code
├── scripts/                # Runnable steps
├── tests/                  # Pytest suite
├── docs/                   # Reference docs
├── integrations/           # Optional tool-specific shims (nothing required)
└── results/                # Run outputs & reports (gitignored)
```

## Creating a new project from this template

**Option A — GitHub UI:** click **"Use this template"**, then clone it.

**Option B — GitHub CLI:**

```bash
gh repo create <owner>/<my-project> --template teasol/harness-template --clone
cd <my-project>
python -m harness status
```

## Documentation

- [AGENTS.md](AGENTS.md) — ground rules every agent (and human) works under
- [docs/plans.md](docs/plans.md) — Tier 1: plans, Planners, adoption, reports
- [docs/orchestration.md](docs/orchestration.md) — Tier 2: plans, tasks, contracts
- [docs/verification.md](docs/verification.md) — specs, checks, `reproduce`
- [docs/reproducibility.md](docs/reproducibility.md) — determinism and provenance
- [docs/architecture.md](docs/architecture.md) — components and design rules
- [agents/planner.md](agents/planner.md) · [agents/worker.md](agents/worker.md) — role contracts
- [integrations/](integrations/README.md) — optional tool shims (nothing required)

## CI

- **CI** (`.github/workflows/ci.yml`): lint + tests on every push/PR.
- **Verification** (`.github/workflows/verify.yml`): the determinism gate
  (`harness reproduce`), plan validity and plan/task drift, re-verification of
  every task marked `done`, and the plan lifecycle. Reports are uploaded as
  build artifacts.
- **Pre-commit** (`.pre-commit-config.yaml`): the same gates locally — run
  `pre-commit install` once per checkout.
