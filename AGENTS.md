# AGENTS.md

Ground rules for AI coding agents (and humans) working in repositories created
from this template. Read this before making any change.

## Repository purpose

This is a harness-engineering template: verification infrastructure
(`harness/`), declarative specs (`configs/`), and CI gates come pre-wired.
Project-specific research code lives alongside it and must keep using the
harness for anything that needs to be trusted or reproduced.

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
| `make reproduce` | Re-run verification into a fresh results dir |
| `make clean` | Remove generated artifacts |

Direct harness usage:

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
