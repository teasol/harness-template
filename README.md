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

Install the harness package into your Python environment and initialize it in your project directory:

```bash
# 1. Install Research Harness:
pip install git+https://github.com/teasol/harness-template.git

# 2. Initialize in your project:
cd my-project
harness init                                   # scaffolds AGENTS.md, agents/, configs/, and configures agent tiers
harness verify --spec configs/demo.yaml        # prove it works here
```

`harness init` scaffolds all necessary agent role contracts (`agents/`), platform configurations (`configs/`), and prompts you to configure each agent tier — you can select **Manual** (copy-pasting briefings into an agent session yourself) or automated **AI platforms** (Antigravity, Claude Code, Codex, opencode, etc.). You can change this selection at any time later with `harness setup`.

### 1. Start an experiment — and its Planner

An experiment has **exactly one Planner** who owns it end to end (and can explore or refine questions/hypotheses within it).
Starting an experiment creates its branch, its isolated working directory (a git *worktree*) under `.experiments/`, a plan skeleton, and prints the Planner's briefing:

```bash
python -m harness exp start your-experiment-name
```

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
python -m harness plan run plans/your-experiment-name.yaml           # Workers build them
```

`plan run` hands each module to a Worker in dependency order, checks acceptance
**and** declared deliverables, and retries with the actual failure output — up
to 6 attempts. A module that still fails is marked `blocked` and handed back to
the Planner; that usually means the brief or the contract is wrong, not that
the Worker is bad.

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
python -m harness exp start baseline
python -m harness exp start your-experiment-name
python -m harness exp list
```

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
- **Human at the end** — the harness reports readiness and stops. Which
  hypothesis enters the record is your decision, and the one thing here that is
  deliberately not automated.

## How it works

Three tiers. You set direction and decide what gets merged; a Planner owns one
experiment end to end; Workers each build one module.

```mermaid
flowchart TD
    R["Tier 1 · Researcher<br/>the question, and the merge decision"]
    P["Tier 2 · Planner<br/>one experiment · own branch + worktree"]
    W["Tier 3 · Workers<br/>one module each · sequential"]
    R -->|"harness exp start --question"| P
    P -->|"tasks with contracts + acceptance"| W
    W -->|"verified deliverables"| P
    P -->|"measured report"| R
    R -->|"git merge — yours alone"| R
```

### Tier 1 ↔ 2 — experiments

An experiment is one hypothesis or investigation branch in its own git worktree, so
several run side by side without colliding.

```bash
python -m harness exp start your-experiment-name                   # creates + briefs the Planner
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

Reports are self-contained by rule: a plan that reads another experiment's
results is rejected, because comparing experiments is your job, done by reading
finished reports. A result that only makes sense beside another cannot be
judged on its own.

Full reference: [docs/experiments.md](docs/experiments.md).

### Tier 2 ↔ 3 — plans, tasks, and Workers

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
seed: 42
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
platform, seed — so a result found months later can be traced back to code.
`harness reproduce` runs a spec twice and diffs a hash manifest of **every**
artifact, and refuses to pass a spec that produced nothing to compare.

Full reference: [docs/verification.md](docs/verification.md) ·
[docs/reproducibility.md](docs/reproducibility.md).

## Everyday commands

```bash
make status        # where am I, what next
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
│   ├── plan.py             #   Plans: module DAGs, contracts, report spec
│   ├── task.py             #   Task lifecycle, board, deliverable enforcement
│   ├── worker.py           #   Agent adapters + the verify-and-retry loop
│   ├── experiment.py       #   Experiments: worktrees, briefings, reports
│   ├── setup.py            #   First-run choice of platform / model / effort
│   └── cli.py              #   status | setup | verify | reproduce | plan | task | exp | planner
├── configs/                # Specs, and which agent runs each tier
│   ├── agents.yaml         #   planner + worker: platform, model, effort
│   └── agent-platforms.yaml#   Platform presets (data, not code)
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
- [docs/experiments.md](docs/experiments.md) — Tier 1 ↔ 2: worktrees, tiers, reports
- [docs/orchestration.md](docs/orchestration.md) — Tier 2 ↔ 3: plans, tasks, contracts
- [docs/verification.md](docs/verification.md) — specs, checks, `reproduce`
- [docs/reproducibility.md](docs/reproducibility.md) — determinism and provenance
- [docs/architecture.md](docs/architecture.md) — components and design rules
- [agents/planner.md](agents/planner.md) · [agents/worker.md](agents/worker.md) — role contracts
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
