# Harness Template

Standard agent-first harness engineering template for **reproducible research**
and **automated verification workflows**.

## Getting started

You state a research question. An AI **Planner** breaks it into modules, hands
each to an AI **Worker**, and verifies the result. Every experiment lives on its
own git branch. When one finishes you get a report — did integration pass, which
modules were built, does it reproduce, and the numbers you asked for — and **you**
decide whether to merge. The harness measures; it never decides.

Lost at any point:

```bash
python -m harness status     # reads the real state, names the next command
```

### 0. Quickstart

```bash
# 1. Install Research Harness:
pip install git+https://github.com/teasol/harness-template.git

# 2. Initialize in your project:
cd my-project
harness init                                   # scaffolds AGENTS.md, agents/, configs/, and configures agent tiers

# 3. Create the Planner that will own your experiments:
harness create -n my-planner --model <model> --effort high
```

`harness init` scaffolds the agent role contracts (`agents/`), platform configurations (`configs/`), and prompts you to configure each agent tier — **Manual** (you paste briefings into an agent session yourself) or an automated **AI platform** (Antigravity, Claude Code, Codex, opencode, …). Change it later with `harness setup`.

**Adopting an existing project?** That is the normal case, and `harness init`
notices: it records how the harness arrived — the commit, and how many source
files predate it, so "not verified yet" has a boundary instead of being a
feeling — and prints the order that works.

```bash
harness project init                       # what a Planner must know here
harness create -n <name> --model <model>   # a Planner that outlives one experiment
harness exp start <name> --planner <name>  # and it plans the rest
```

The first Planner's briefing opens with the situation: none of this code is
covered by a contract or an acceptance check yet, and making it verifiable is
its first experiment. **The harness does not prescribe how to modularize an
existing codebase** — deciding the decomposition is the Planner's job, and a
pipeline baked into the tool would be wrong for the next project anyway. The
briefing supplies only what generalizes: the conditions a module boundary has
to satisfy, and the ordering principle that in research code the artifact of
record is a *measurement*, so pin the numbers before anything moves. See
[docs/experiments.md](docs/experiments.md#adopting-an-existing-project).

### 1. Register a Planner, then start an experiment

A Planner runs **many** experiments; each experiment has **one** Planner, who
owns it end to end and can refine the question within it. **The Planner comes
first** — it outlives any one experiment.

```bash
python -m harness create -n your-planner --model <model> --effort high
python -m harness exp start your-experiment-name --planner your-planner
```

A registered Planner outlives one experiment. Starting an experiment under one
means it inherits that Planner's model — so its report is never "model not
recorded", and two runs planned by different models can be told apart — and its
briefing opens with everything that Planner already learned in this project.
Register afterwards and the first briefing has already been written without any
of it.

`exp start` creates the branch, an isolated working directory (a git *worktree*)
under `.experiments/`, a plan skeleton, and prints the Planner's briefing:

Example command output:

```text
Experiment 'your-experiment-name' created on exp/your-experiment-name.
Its Planner is 'planner'. Everything below is that Planner's briefing —
follow it, or hand this session to whoever will.

======================================================================
# Planner briefing: your-experiment-name

## Question

**Not settled yet.** That is normal — a question gets sharper by
talking it through. Work it out with the researcher first: what is
being asked, what would count as an answer, what they want reported.
Plan nothing and spawn no Worker until you agree, then record it
verbatim so it survives this session and reaches the report.

## State

**question unsettled** — the question has not been agreed with the researcher yet

## Next

  harness exp question your-experiment-name --set "<their question, verbatim>"

## Your role

Read agents/planner.md. In short:
  - You own plans/your-experiment-name.yaml, the module DAG, typed contracts,
    and the experiment report the researcher asked for.
  - Work inside .experiments/your-experiment-name.
  - Never write module code yourself; hand each module off to a Worker.
```

👉 **Copy and paste everything below the separator (`====`) into your AI agent session** (e.g. Antigravity, Claude Code, Cursor, Codex, etc.) to brief the Planner on its role and current tasks. (Or, if an unattended coding agent runs the command itself, it is already briefed.)

An experiment can explore multiple questions as you work through it. You do not need the question phrased yet — one usually gets sharper by talking it through, so the briefing tells the Planner to settle it with you first, and to plan nothing and spawn no Worker until you agree. When it settles:

```bash
python -m harness exp question your-experiment-name --set "<what you agreed on>"
```

If you already know the question, pass it up front — `exp start your-experiment-name --question "..."`. That is also what an unattended `planner run` needs, since a spawned Planner has nobody to ask.

### 2. Stay oriented

```bash
python -m harness planner brief your-experiment-name
```

The same briefing, re-read from current state. It always has the same sections
— **Question**, **State**, **Next**, **Your role** — so you skim it rather than
re-read it, and `Next` always names the one command to run now: settle the
question, validate the plan, run the Workers, or write the report.

### 3. Let the Planner work

Once you agree on the question, the Planner writes the plan and drives it:

```bash
python -m harness plan validate plans/your-experiment-name.yaml
python -m harness plan materialize plans/your-experiment-name.yaml   # one task per module
python -m harness plan approve plans/your-experiment-name.yaml --by you
python -m harness plan run plans/your-experiment-name.yaml           # Workers build them
```

**A plan is a proposal until you approve it, and `plan run` requires that.**
Approving prints what it commits you to — the module list and the worst-case
agent time, which is otherwise invisible: two modules at six attempts and a
30-minute cap is a six-hour ceiling. The approval is fingerprinted against the
plan's contents, so editing the plan lapses it.

`plan run` hands each module to a Worker in dependency order, checks acceptance
**and** declared deliverables, and retries with the actual failure output — up
to 6 attempts. It stops early when it stops making progress: three attempts in
a row that change no deliverable hand back to the Planner rather than spend the
rest of the cap repeating one failure. A module that fails is `blocked` and
handed back; that usually means the brief or the contract is wrong, not that
the Worker is bad.

A module marked `executor: planner` is never handed to a Worker. Work that runs
an experiment or reads a log gains nothing from Worker isolation, and briefing
an agent to do it costs more than doing it — `plan run` skips those and names
them as yours.

**Watching a long run.** Steps and Worker attempts buffer their output until
they exit, so from a second terminal:

```bash
python -m harness progress --watch
```

```text
worker primary7-runner (module 1/2 · attempt 2/6) · running 12m30s · 17m30s before the cap
```

A heartbeat that stopped ticking is reported as **dead**, not slow — the
distinction you actually need during a long wait.

**Agents are manual until you configure them** — out of the box the harness
writes a briefing and stops. `make agents-setup` (or `harness setup`) picks the
platform, model, and reasoning level for each tier; see
[Choosing the tiers](#choosing-the-tiers) for why that choice is the point.
With both tiers configured you can also skip sitting in the experiment
altogether:

```bash
python -m harness planner run your-experiment-name   # spawns the Planner, drives it to a report
```

That needs a recorded question, since a spawned Planner has nobody to ask.

### 4. Read the report and decide

```bash
python -m harness exp report your-experiment-name --determinism --save
```

```
[your-experiment-name] READY TO MERGE
  integration: PASSED
  tasks:       2/2 done
  determinism: REPRODUCIBLE
  commit:      1ebd0bed...
  retained_mass: 0.198128
```

**`READY TO MERGE` means the harness could not find anything wrong — not that
the result is interesting.** That judgement is yours. `NOT READY` lists exactly
what is missing.

Then, if you want it:

```bash
git merge exp/your-experiment-name
```

Nothing merges on your behalf. Decide against an experiment and simply don't
merge — the branch remains, so the attempt stays on the record.

### Running several at once

```bash
python -m harness exp start baseline --planner your-planner
python -m harness exp start your-experiment-name --planner your-planner
python -m harness exp list
```

One Planner can own several experiments — that is the point of registering it
separately from any of them.

Each report stands on its own: an experiment may not read another's results,
and the harness rejects a plan that tries. Comparing them is **your** job, done
by reading the finished reports — a result that only makes sense beside another
one cannot be judged.

### When something goes wrong

| Symptom | What it means |
| --- | --- |
| The briefing says the question is **not settled** | Agree on it with the researcher, then `harness exp question <name> --set "..."`. |
| `plan validate` says "still the scaffold" | The TODOs have not been filled in yet. |
| A task is `blocked` | A Worker used up its attempts. Read the task log; usually the brief is ambiguous. |
| `exp report` says `NOT READY` | It lists every blocker. Fix them — or decide the experiment failed, which is a valid outcome. |
| `NOT REPRODUCIBLE` | Something is unseeded. See [docs/reproducibility.md](docs/reproducibility.md). |
| `planner run` says **NEEDS_HUMAN** | No question is recorded. Record one, or drive the experiment interactively. |
| `cost: not measured` | Expected. The harness does not estimate token spend. |
| `plan run` says **this plan has never been approved** | Read it, then `harness plan approve <plan> --by <you>`. Editing a plan lapses its approval. |
| A task aborted: **the Worker changed the harness** | Containment did its job. Acceptance under a rewritten harness proves nothing. Review the listed files and revert. |
| A task stopped after **3 attempts changed no deliverable** | The Worker is wedged, not slow. The brief or the acceptance is wrong — that is yours to fix. |
| A Worker exited in **under 5 seconds** | A misconfigured command, not a coding problem. `harness setup --check`. |
| `plan run` printed **0 task(s) run** | It now lists why each module is not running — blocked, waiting, claimed, or `executor: planner` (yours). |
| A long run looks hung | `harness progress --watch` from another terminal. |

## Why

Research code rots because verification is ad-hoc: "run this notebook, eyeball
the numbers." This template makes verification a first-class artifact, and
then builds an agent workflow on top of it:

- **Declarative** — what to run and what to check lives in YAML, not tribal memory.
- **Deterministic** — seeds are explicit; every run's artifacts are hash-compared.
- **Enforced** — anything declared (deliverables, dependencies, report sources)
  is machine-checked. A rule the harness cannot check is prose, and prose is
  not a gate.
- **Measured, not narrated** — an agent says *where* a number lives; the
  harness reads the artifact. No result reaches you on an agent's word.
- **Contained** — a Worker that edits the harness, or a tracked file it never
  declared, fails the task. Acceptance run under a harness the agent just
  rewrote proves nothing, so this is enforced rather than asked.
- **Human at the end** — the harness reports readiness and stops. Which
  hypothesis enters the record is your decision, and the one thing here that is
  deliberately not automated.

## How it works

**Two tiers.** You and the Planner settle the question and you decide what gets
merged. Everything else happens inside one experiment branch, where the Planner
is also the **Main Worker**: it implements the core work itself and delegates
routine bulk to a **Sub-Worker**, one at a time. One Planner runs many
experiments over the life of a project.

```mermaid
flowchart TD
    subgraph T1 ["Tier 1 · research strategy and decision"]
        direction LR
        U(["👤 Researcher"]) <-->|"research question · dialogue"| P["🧠 Planner"]
    end

    subgraph T2 ["Tier 2 · serial experimentation"]
        direction TB
        subgraph BR ["🌿 dedicated branch — exp/idea-v1"]
            direction TB
            MW["🤖 Main Worker — the Planner itself<br/>core logic · planning · orchestration"]
            subgraph EX ["serial execution"]
                direction TB
                SELF["executor: main<br/>direct implementation"]
                SUB["⚙️ Sub-Worker<br/>executor: sub — long coding, log parsing"]
            end
            MW -->|"does it itself"| SELF
            MW -->|"spawns, one at a time"| SUB
            SUB -->|"returns output"| MW
        end
        H["⚙️ Harness<br/>integration · module acceptance<br/>reproducibility · metric extraction"]
    end

    P ==>|"initiates each experiment"| MW
    EX ==>|"completed run"| H
    H ==>|"report — metrics and status"| P
    P ==>|"final report"| U
    U --> DEC{"merge to main?"}
    DEC -->|"yes"| MAIN[("🌿 main")]
    DEC -->|"no"| NEXT["next experiment · iterate"]
    NEXT --> P
```

The harness verifies whatever comes out, whoever produced it: a module the
Planner wrote itself carries the same contract and the same acceptance as one a
Sub-Worker built. What the split changes is who writes the code, never whether
it is checked.

### Tier 1 — experiments and the merge decision

An experiment is one hypothesis or investigation branch in its own git worktree, so
several run side by side without colliding.

```bash
python -m harness create -n <name> --model <model>                 # a Planner across experiments
python -m harness exp start your-experiment-name --planner <name>  # creates + briefs the Planner
python -m harness exp question your-experiment-name --set "..."   # once you have settled it
python -m harness planner brief your-experiment-name              # current state, any time
python -m harness planner run your-experiment-name                # or spawn one unattended
python -m harness exp list                                        # what is in flight
python -m harness exp report your-experiment-name --determinism --save
git merge exp/your-experiment-name                                # only you do this
```

`exp report` measures the spine itself — integration result, per-task
acceptance re-verified, determinism, the exact commit to merge, and an explicit
list of what went **unverified** — then extracts the metrics you asked for from
real run artifacts. `READY TO MERGE` means the harness found nothing wrong, not
that the result is interesting.

A Planner made with `harness create` outlives one experiment: it carries
its model — so a report is never "model not recorded" — and the notes it wrote
in earlier runs, which is the hour it spent learning the project not being paid
twice. `harness planner note <name> --add "..."` records something the next run
should not have to rediscover. Durable project policy belongs in
`configs/project.yaml` instead, which you own: one is a lab notebook, the other
is the rules.

Reports are self-contained by rule: a plan that reads another experiment's
results is rejected, because comparing experiments is your job, done by reading
finished reports. A result that only makes sense beside another cannot be
judged on its own.

Full reference: [docs/experiments.md](docs/experiments.md).

### Tier 2 — plans, tasks, and Sub-Workers

The Planner decomposes the goal into modules with typed contracts and
machine-checkable acceptance, then hands each to a Worker through the harness —
never by hand.

```mermaid
flowchart TD
    P["Planner"] -->|"writes"| PL["plans/*.yaml<br/>goal · DAG · contracts · report"]
    PL -->|"materialize"| T["tasks/*.task.yaml<br/>self-contained work orders"]
    T -->|"plan run"| W["Workers"]
    W -->|"implement"| S["src/… deliverables"]
    S -->|"acceptance + deliverables"| H["Runner + checks"]
    H -->|"pass → done · fail → retry · exhausted → blocked"| T
    T -->|"all done"| I["integration spec"]
```

```bash
python -m harness plan validate plans/your-experiment-name.yaml
python -m harness plan materialize plans/your-experiment-name.yaml   # one task per module
python -m harness plan run plans/your-experiment-name.yaml           # Workers build them
```

`plan run` invokes a Worker per module in dependency order, verifies acceptance
**and** declared deliverables, and retries with the real failure output — up to
a cap (default 6). A module that still fails is `blocked` and handed back to
the Planner, which usually means the brief is wrong rather than the Worker.

Workers run **one at a time** within an experiment. Isolation belongs at the
experiment level: a plan's DAG is near-linear so concurrency buys little, while
per-Worker branches would fracture the task board and make dependency gates
read stale state.

- **Planner contract**: [agents/planner.md](agents/planner.md)
- **Worker contract**: [agents/worker.md](agents/worker.md)
- **Full reference**: [docs/orchestration.md](docs/orchestration.md)

### Choosing the tiers

```bash
python -m harness setup --list      # platforms available
python -m harness setup --check     # can each tier actually be driven?
python -m harness setup             # interactive, or:
python -m harness setup \
  --planner-platform claude --planner-model claude-opus-5 --planner-effort high \
  --worker-platform  claude --worker-model  claude-haiku-4-5-20251001 --worker-effort low
```

**A tier you cannot choose is not a tier.** A Worker's task is bounded and
fully specified, so a small fast model usually suffices; reserving the
expensive one for planning is the whole economic argument for splitting them.
Both tiers land in `configs/agents.yaml` side by side, and both are recorded in
the report, so which model built what is auditable rather than merely intended.

`setup --check` sends one cheap prompt to each configured tier and reports
whether the agent actually received it. A preset that does not match the
installed CLI otherwise fails silently — six attempts in under a second each,
with the agent never having seen the task.

Platform knowledge lives in `configs/agent-platforms.yaml` as **data** — adding
your lab's tool, or a local model, is an entry there, not a change to
`harness/`. `--planner-session <id>` attaches a tier to a session you already
have open. The default adapter is `manual`: the harness writes a briefing and
stops, so the template works with no API key.

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
name: my-experiment
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

A task's acceptance is a spec run by this same Runner, and an experiment's
report is that machinery run over a whole plan. One engine, three altitudes.

Every report records its provenance — commit, dirty flag, interpreter,
platform, seed, and every environment variable the harness injected — so a
result found months later can be traced back to code. `seed:` seeds and nothing
more: deterministic GPU math constrains kernel selection and therefore changes
results, so it is opt-in via `deterministic_math` rather than something a seed
turns on behind your back.
`harness reproduce` runs a spec twice and diffs a hash manifest of **every**
artifact, and refuses to pass a spec that produced nothing to compare.

Full reference: [docs/verification.md](docs/verification.md) ·
[docs/reproducibility.md](docs/reproducibility.md).

## Everyday commands

```bash
make status        # where am I, what next
python -m harness progress --watch   # what is running right now (second terminal)
make agents-setup  # choose platform / model / reasoning level per tier
make verify        # run the verification spec end-to-end
make reproduce     # run it twice and diff every artifact (determinism gate)
make run           # put every ready task through a Worker
make test          # pytest suite
make lint          # ruff check + format check
make audit         # re-verify every task marked done
make drift         # validate every plan, fail on task/plan drift
make experiments   # list experiment branches/worktrees
```

`make setup` installs dependencies and configures agent tiers (Manual or AI); `make agents-setup` allows re-configuring or switching agents at any time.

## Project structure

```
harness-template/
├── AGENTS.md               # Agent-facing ground rules (read this first)
├── Makefile                # status / setup / verify / reproduce / run / audit / drift
├── pyproject.toml          # Project metadata + tool config
├── .experiments/           # Experiment worktrees (gitignored, one per hypothesis)
├── agents/                 # Role contracts for hierarchical agents
│   ├── planner.md          #   Planner: owns plans, DAGs, contracts, the report
│   └── worker.md           #   Worker: owns one module task, in isolation
├── plans/                  # Orchestration plans (Planner output)
├── tasks/                  # Materialized work orders + lifecycle (Worker input)
├── harness/                # The harness itself (stdlib + PyYAML only)
│   ├── spec.py             #   Spec loading & validation
│   ├── runner.py           #   Step execution engine
│   ├── checks.py           #   Built-in checks + registry
│   ├── report.py           #   JSON/Markdown report generation
│   ├── reproduce.py        #   Determinism gate: repeat a spec, diff artifacts
│   ├── reproducibility.py  #   Seeding, hashing, run provenance
│   ├── plan.py             #   Plans: module DAGs, contracts, approval, report spec
│   ├── task.py             #   Task lifecycle, board, deliverable enforcement
│   ├── worker.py           #   Agent adapters + the verify-and-retry loop
│   ├── guard.py            #   Containment: the harness and undeclared files are off-limits
│   ├── heartbeat.py        #   Where a run is right now, readable from another terminal
│   ├── experiment.py       #   Experiments: worktrees, briefings, reports
│   ├── project.py          #   What a Planner must know about THIS project
│   ├── planners.py         #   Planners that outlive one experiment, and their notes
│   ├── adoption.py         #   Landing on a codebase that already exists
│   ├── setup.py            #   First-run choice of platform / model / effort
│   └── cli.py              #   status | progress | setup | verify | plan | task | exp | planner | project
├── .harness/               # Harness state that is not project code
│   ├── configs/agents.yaml #   planner + worker: platform, model, effort
│   ├── configs/project.yaml#   authoritative docs, report format, conventions
│   ├── planners/           #   registered Planners and what they have learned
│   └── adoption.json       #   how the harness arrived, if code predated it
├── src/                    # Project code (demo_pipeline ships as the example)
├── scripts/                # Runnable steps (bootstrap, demo, instantiate)
├── tests/                  # Pytest suite (incl. end-to-end harness tests)
├── docs/                   # Reference docs
├── integrations/           # Optional tool-specific shims (nothing required)
├── data/                   # Datasets (gitignored; see data/README.md)
├── results/                # Run outputs & reports (gitignored)
└── .github/                # CI workflows, issue/PR templates
```

Put project-specific code in a package of your choice (e.g. `src/<project>/`);
the `harness` package is infrastructure and stays as-is.

## Creating a new project from this template

**Option A — GitHub UI:** click **"Use this template"** on the repo page, then
clone the generated repository.

**Option B — GitHub CLI:**

```bash
gh repo create <owner>/<new-project> --template teasol/harness-template --clone
cd <new-project>
```

Then follow the walkthrough at the top of this file — or just run:

```bash
python -m harness status
```

## Documentation

- [AGENTS.md](AGENTS.md) — ground rules every agent (and human) works under
- [docs/experiments.md](docs/experiments.md) — Tier 1: worktrees, Planners, reports
- [docs/orchestration.md](docs/orchestration.md) — Tier 2: plans, tasks, contracts
- [docs/verification.md](docs/verification.md) — specs, checks, `reproduce`
- [docs/reproducibility.md](docs/reproducibility.md) — determinism and provenance
- [docs/architecture.md](docs/architecture.md) — components and design rules
- [agents/planner.md](agents/planner.md) · [agents/worker.md](agents/worker.md) — Planner/Main Worker and Sub-Worker contracts
- [integrations/](integrations/README.md) — optional tool shims (nothing required)

## CI

- **CI** (`.github/workflows/ci.yml`): lint + tests on every push/PR.
- **Verification** (`.github/workflows/verify.yml`): the determinism gate
  (`harness reproduce`), plan validity and plan/task drift, re-verification of
  every task marked `done`, then the integration spec. Reports are uploaded as
  build artifacts.
- **Pre-commit** (`.pre-commit-config.yaml`): the same gates locally — run
  `pre-commit install` once per checkout. Tool-agnostic by design, so the rules
  bind humans and any coding agent identically.
