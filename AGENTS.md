# AGENTS.md

Ground rules for AI coding agents (and humans) working in repositories created
from this template. Read this before making any change.

## Repository purpose

This is a harness-engineering template with **two-tier agent orchestration**:
a Planner agent owns direction and flow (`plans/`), Worker agents each own one
module task (`tasks/`), and the harness enforces every contract
machine-checkably. Project-specific research code lives alongside it and must
keep using the harness for anything that needs to be trusted or reproduced.

## Orchestration roles

You are always acting in ONE of three roles — know which:

- **Planner** ([agents/planner.md](agents/planner.md)): own `plans/*.yaml`,
  module DAGs, contracts, acceptance, and the integration spec. Never write
  module code. Hand off via `harness plan materialize`.
- **Worker** ([agents/worker.md](agents/worker.md)): claim exactly one task
  (`harness task claim`), implement it fully against the task file's brief
  and contract, verify (`harness task verify`), mark done (`harness task
  done`). Never touch other modules, the plan, or `harness/`.
- **Maintainer** (default): work on the harness itself, CI, or docs. Follow
  the rules below.

If a Worker finds a contract ambiguous or a dependency broken: `harness task
block --reason "..."` and hand back to the Planner. Never improvise the plan.

## Non-negotiable rules

1. **Verify before you finish.** A task is not done until
   `make lint && make test && make verify` all pass. If your change touches
   determinism (seeds, data loading, model code), also run `make reproduce`.
2. **Never commit artifacts.** `data/` (except `data/README.md`) and `results/`
   (except `results/README.md`) are gitignored. Never commit checkpoints,
   logs, or datasets.
3. **Determinism first.** Every source of randomness must be seeded — via the
   spec's `seed`, the harness env vars, or explicit config. No unseeded
   `random`/`numpy`/`torch` calls in committed code paths.
4. **Declarative verification.** Prefer adding a check to a spec
   (`configs/*.yaml`) over writing one-off validation scripts. New behavior
   that produces outputs should come with new checks.
5. **Document user-facing changes** in `CHANGELOG.md`.

## Standard commands

| Command | Purpose |
| --- | --- |
| `make setup` | Editable install + dev tools |
| `make lint` | `ruff check` + format check |
| `make format` | Auto-format with ruff |
| `make test` | Pytest suite |
| `make verify` | Run the verification spec (`configs/demo.yaml`) |
| `make plan` | Validate the orchestration plan + refresh task files |
| `make tasks` | Show the task board |
| `make reproduce` | Re-run verification into a fresh results dir |
| `make clean` | Remove generated artifacts |

Orchestration commands:

```bash
python -m harness plan validate|materialize|status plans/<plan>.yaml
python -m harness task list|show|claim|block|verify|done --id <id>
```

Diragents/` — role contracts (planner.md, worker.md). Read the one for your role.
- `plans/` — orchestration plans: goal, module DAG, contracts, briefs, acceptance.
- `tasks/` — materialized work orders with lifecycle state (status/worker/log). Committed.
- `harness/` — verification + orchestration engine. Stable, minimal, stdlib + PyYAML only.
- `src/` — project code (`demo_pipeline/` ships as the orchestration example)

```bash
python -m harness verify --spec configs/<spec>.yaml [--results-dir DIR]
python -m harness hash <file>          # sha256 helper
```

## Where things live

- `harness/` — verification engine. Stable, minimal, stdlib + PyYAML only.
- `configs/` — specs (`name`, `seed`, `steps`) and experiment configs. Data, not logic.
- `scripts/` — runnable steps referenced by specs.
- `tests/` — pytest; must include coverage for any new check type.
- `docs/` — reference docs; update when behavior changes.
- `results/runs/<spec>-<timestamp>/` — reports (`report.json`, `report.md`, `logs/`). Gitignored.

## How verification works

A spec is an ordered list of steps; each step runs a shell command and is
followed by checks. The runner exports these env vars to every step and
expands `${VAR}` in check `path` params:

- `HARNESS_RESULTS_DIR` — directory for this run's artifacts
- `HARNESS_RUN_ID` — spec name

See `docs/verification.md` for the full spec and check reference.

## Extending the harness

To add a check type:

1. Implement `check_<name>(root: Path, params: dict) -> str` in `harness/checks.py`
   (raise `CheckError` on failure; return a short detail string on success).
2. Register it in `CHECK_REGISTRY`.
3. Add tests in `tests/test_checks.py`.
4. Document params in `docs/verification.md`.

## Conventions

- Python ≥ 3.10. Harness core uses only the stdlib + PyYAML — do not add heavy
  dependencies to `harness/`.
- ruff-enforced style, line length 100.
- Configs are data (YAML); logic lives in code. No logic in configs.
- Commit messages: conventional style (`feat:`, `fix:`, `docs:`, `chore:`).

## CI gates

- `ci.yml` — lint + tests (Python 3.10–3.12) on every push/PR.
- `verify.yml` — runs the harness twice per PR and diffs output hashes
  (determinism gate). If this fails, your change introduced nondeterminism —
  fix it before merging; do not delete the workflow.
