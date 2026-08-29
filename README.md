# Harness Template

Standard agent-first harness engineering template for **reproducible research**
and **automated verification workflows**.

Every lab project should be created from this repository as a template. It ships
with a small, dependency-light verification harness that turns declarative YAML
specs into executable, checkable, report-producing pipelines — runnable locally
via `make` and enforced in CI.

## Why

Research code rots because verification is ad-hoc: "run this notebook, eyeball
the numbers." This template makes verification a first-class artifact:

- **Declarative** — what to run and what to check lives in `configs/*.yaml`, not tribal memory.
- **Deterministic** — seeds are explicit; CI re-runs pipelines and compares hashes.
- **Agent-first** — `AGENTS.md` gives AI coding agents (and humans) the same ground rules, and `make verify` is the machine-checkable definition of "done."
- **Reportable** — every run produces `report.json` + `report.md` under `results/runs/`.

## Quickstart

```bash
make setup     # editable install + dev tools (or: bash scripts/bootstrap.sh)
make verify    # run the demo verification spec end-to-end
make reproduce # run it twice and diff every artifact (determinism gate)
make test      # pytest suite (includes harness end-to-end tests)
make lint      # ruff check + format check
make audit     # re-verify every task marked done
make drift     # fail if task files have drifted from the plan
```

`make verify` runs `configs/demo.yaml`: a seeded step that produces
`output.json`, verified by checks (`file_exists`, `json_metric`), with a report
written to `results/runs/<name>-<timestamp>/`.

## How verification works

```mermaid
flowchart LR
    A["Spec YAML<br/>configs/*.yaml"] -->|"load_spec"| B["Runner"]
    B -->|"subprocess"| C["Steps<br/>scripts / experiments"]
    C --> D["Artifacts<br/>results/runs/..."]
    D -->|"run_check"| E["Checks<br/>file_exists, json_metric, ..."]
    E --> F["Report<br/>report.json / report.md"]
    F --> G["CI gate<br/>(Verification workflow)"]
```

A minimal spec:

```yaml
name: my-experiment
seed: 42
steps:
  - id: train
    run: python scripts/train.py --config configs/default.yaml
    timeout: 3600
    checks:
      - type: file_exists
        path: ${HARNESS_RESULTS_DIR}/metrics.json
      - type: json_metric
        path: ${HARNESS_RESULTS_DIR}/metrics.json
        metric: val.accuracy
        min: 0.5
```

Run it with `python -m harness verify --spec configs/my-experiment.yaml`.
Full reference: [docs/verification.md](docs/verification.md).

## Three-tier research workflow

The researcher sets direction and decides what enters the record; a Planner
owns one experiment end to end; Workers each implement one module.

```mermaid
flowchart TD
    R["Tier 1 · Researcher<br/>defines the question, decides the merge"]
    P["Tier 2 · Planner<br/>one experiment, own branch + worktree"]
    W["Tier 3 · Workers<br/>one module each, sequential"]
    R -->|"instruction"| P
    P -->|"tasks with contracts"| W
    W -->|"acceptance + deliverables"| P
    P -->|"measured report"| R
    R -->|"git merge (researcher's call)"| R
```

Each experiment gets its own branch and git worktree, so several hypotheses run
side by side without colliding:

```bash
python -m harness exp start sparse-attn      # branch exp/sparse-attn + worktree
python -m harness exp list                   # what is in flight
python -m harness exp report sparse-attn --determinism --save
git merge exp/sparse-attn                    # only the researcher does this
```

The Planner hands modules to Workers through the harness, not by hand:

```bash
python -m harness planner brief sparse-attn --register session-01  # become the Planner
python -m harness plan run plans/sparse-attn.yaml                  # drain the ready queue
```

`task run` invokes the configured Worker, verifies acceptance and deliverables,
and retries with the real failure output up to a cap (default 6) — then blocks
the task for the Planner. Workers are configured in `configs/worker.yaml`; the
default writes a briefing for a human, and `adapter: cli` points at whichever
headless coding agent your lab uses. The harness names no vendor.

`exp report` measures the spine itself — integration result, per-task
acceptance, determinism, the commit to merge, and what went **unverified** —
and extracts the metrics the researcher asked for from real run artifacts. The
Planner declares *where* each number lives; it never supplies a value. Reports
are self-contained by rule: a plan that reads another experiment's results is
rejected, because comparing experiments is the researcher's job.

Full reference: [docs/experiments.md](docs/experiments.md).

## Two-tier agent orchestration

Beyond single pipelines, this template orchestrates **multiple agents
hierarchically**: a **Planner** owns direction and flow; **Workers** each own
one module, built in isolation against a self-contained spec.

```mermaid
flowchart TD
    P["Planner agent"] -->|"writes"| PL["plans/*.yaml<br/>goal · DAG · contracts"]
    PL -->|"materialize"| T["tasks/*.task.yaml"]
    T -->|"claim"| W["Worker agents"]
    W -->|"implement + verify"| S["src/... deliverables"]
    S -->|"task done"| T
    T -->|"all done"| I["integration spec"]
    I --> H["Runner + checks"]
```

Try the shipped example end-to-end:

```bash
make plan        # validate plans/demo-pipeline.yaml + refresh tasks
make tasks       # show the board
python -m harness task show --id stats        # a Worker's complete work order
python -m harness verify --spec configs/demo-pipeline.yaml   # Planner's integration gate
```

- **Planner contract**: [agents/planner.md](agents/planner.md)
- **Worker contract**: [agents/worker.md](agents/worker.md)
- **Full reference**: [docs/orchestration.md](docs/orchestration.md)

## Project structure

```
harness-template/
├── AGENTS.md               # Agent-facing ground rules (read this first)
├── Makefile                # setup / lint / test / verify / plan / tasks
├── pyproject.toml          # Project metadata + tool config
├── .experiments/           # Experiment worktrees (gitignored, one per hypothesis)
├── agents/                 # Role contracts for hierarchical agents
│   ├── planner.md          #   Planner: owns plans, DAGs, contracts, flow
│   └── worker.md           #   Worker: owns one module task, in isolation
├── plans/                  # Orchestration plans (Planner output)
│   └── demo-pipeline.yaml
├── tasks/                  # Materialized work orders (Worker input)
├── harness/                # Core verification harness (Python package)
│   ├── spec.py             #   Spec loading & validation
│   ├── runner.py           #   Step execution engine
│   ├── checks.py           #   Built-in checks + registry
│   ├── report.py           #   JSON/Markdown report generation
│   ├── reproducibility.py  #   Seeding & hashing utilities
│   ├── experiment.py       #   Experiments: worktrees, branches, reports
│   ├── worker.py           #   Worker adapters + the retry loop
│   ├── plan.py             #   Plans: module DAGs + contracts + report spec
│   ├── task.py             #   Task lifecycle, board, materialization
│   └── cli.py              #   `python -m harness verify|hash|plan|task`
├── src/                    # Project code (demo_pipeline ships as example)
├── configs/                # Verification & integration specs (YAML)
├── scripts/                # Runnable steps (bootstrap, demo, instantiate)
├── tests/                  # Pytest suite (incl. end-to-end harness tests)
├── docs/                   # Architecture & reference docs
├── integrations/           # Optional tool-specific shims (nothing required)
├── data/                   # Datasets (gitignored; see data/README.md)
├── results/                # Run outputs & reports (gitignored)
└── .github/                # CI workflows, issue/PR templates
```

Put project-specific code in a package of your choice (e.g. `src/<project>/`);
the `harness` package is verification infrastructure and stays as-is.

## Creating a new project from this template

**Option A — GitHub UI:** click **"Use this template"** on the repo page, then
clone the generated repository.

**Option B — GitHub CLI:**

```bash
gh repo create <owner>/<new-project> --template teasol/harness-template --clone
cd <new-project>
```

Then instantiate:

```bash
python3 scripts/instantiate.py --name <new-project>
git add -A && git commit -m "chore: instantiate from harness-template"
make setup && make verify
```

## Documentation

- [AGENTS.md](AGENTS.md) — ground rules for agents & contributors
- [docs/experiments.md](docs/experiments.md) — experiments, worktrees, reports
- [docs/orchestration.md](docs/orchestration.md) — two-tier orchestration reference
- [docs/verification.md](docs/verification.md) — spec & check reference
- [docs/reproducibility.md](docs/reproducibility.md) — determinism policy
- [docs/architecture.md](docs/architecture.md) — component overview

## CI

- **CI** (`.github/workflows/ci.yml`): lint + tests on every push/PR.
- **Verification** (`.github/workflows/verify.yml`): the determinism gate
  (`harness reproduce`), plan validity and plan/task drift, re-verification of
  every task marked `done`, then the integration spec. Reports are uploaded as
  build artifacts.
- **Pre-commit** (`.pre-commit-config.yaml`): the same gates locally — run
  `pre-commit install` once per checkout. Tool-agnostic by design, so the rules
  bind humans and any coding agent identically.
