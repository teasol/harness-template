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
make test      # pytest suite (includes harness end-to-end tests)
make lint      # ruff check + format check
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

## Project structure

```
harness-template/
├── AGENTS.md               # Agent-facing ground rules (read this first)
├── Makefile                # setup / lint / test / verify / reproduce
├── pyproject.toml          # Project metadata + tool config
├── harness/                # Core verification harness (Python package)
│   ├── spec.py             #   Spec loading & validation
│   ├── runner.py           #   Step execution engine
│   ├── checks.py           #   Built-in checks + registry
│   ├── report.py           #   JSON/Markdown report generation
│   ├── reproducibility.py  #   Seeding & hashing utilities
│   └── cli.py              #   `python -m harness verify|hash`
├── configs/                # Declarative specs & experiment configs (YAML)
├── scripts/                # Runnable steps (bootstrap, demo, instantiate)
├── tests/                  # Pytest suite (incl. end-to-end harness test)
├── docs/                   # Architecture & reference docs
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
python scripts/instantiate.py --name <new-project>
git add -A && git commit -m "chore: instantiate from harness-template"
make setup && make verify
```

## Documentation

- [AGENTS.md](AGENTS.md) — ground rules for agents & contributors
- [docs/verification.md](docs/verification.md) — spec & check reference
- [docs/reproducibility.md](docs/reproducibility.md) — determinism policy
- [docs/architecture.md](docs/architecture.md) — component overview

## CI

- **CI** (`.github/workflows/ci.yml`): lint + tests on every push/PR.
- **Verification** (`.github/workflows/verify.yml`): runs the harness twice and
  compares output hashes — a determinism gate on every PR.
