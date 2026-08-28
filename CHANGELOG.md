# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `harness reproduce --spec S [--times N]`: runs a spec repeatedly and diffs a
  hash manifest of every artifact it produced (excluding harness bookkeeping).
  Exits non-zero on divergence, and refuses to pass a spec that produced
  nothing to compare. `make reproduce` and the CI determinism gate now use it.
- `harness task verify --all [--status S]`: audit the whole board in one
  command. CI uses `--status done` so every task claiming completion is
  re-verified, and new tasks are gated without editing the workflow.
- `harness plan status --check`: fails when task files have drifted from the
  plan that spawned them (a plan edit without re-materialization leaves
  Workers reading replaced instructions). Also `make drift`.
- `make audit`, `make drift`; CI uploads reports as build artifacts.
- Pre-commit now enforces pytest, plan validity, plan/task drift, and
  `harness verify` — deliberately tool-agnostic, so the rules bind humans and
  any coding agent identically.
- `tests/test_cli.py`: 25 tests covering the CLI's exit-code contract
  (previously the largest module had no coverage).

- Run provenance: every `report.json`/`report.md` records the git commit,
  branch, dirty flag, Python version and interpreter, platform, harness
  version, and the declared seed.
- `HARNESS_PYTHON` and `HARNESS_SEED` are exported to every step, so specs
  never hardcode a `python` binary or duplicate the spec's `seed`.
- `harness task claim` refuses tasks whose dependencies are not `done`;
  `--force` overrides and records the override in the task log.
- Declared `deliverables` are verified as part of `task verify`/`task done`.

### Fixed

- `plan materialize --force` no longer erases `status`, `worker`, and `log`.
  It refreshes the task's spec from the plan and appends a re-materialization
  entry — previously it silently destroyed the board, which `agents/planner.md`
  explicitly told the Planner to do on every contract change.
- `Makefile` defaults to `python3`; a plain `make verify` failed on
  Debian/Ubuntu checkouts, which ship no `python` binary.
- `harness task done` now honours `--root` and `--results-dir` (they were
  parsed and ignored).
- Restored the `README.md` and `AGENTS.md` sections truncated by 8ecdcdf
  (README "Then instantiate"/"Documentation" and a duplicated CI section;
  AGENTS.md's directory layout heading).

## [0.2.0] - 2026-08-28

### Added

- Two-tier agent orchestration: Planner plans (`plans/*.yaml`) with module
  DAGs, typed IO contracts, worker briefs, and per-module acceptance;
  materialized into self-contained Worker task files (`tasks/*.task.yaml`).
- Task lifecycle: `harness task list|show|claim|block|verify|done` with an
  append-only log and a git-committed board.
- Plan commands: `harness plan validate|materialize|status` (schema + DAG
  validation, cycle detection, acceptance check-type validation).
- Role contracts: `agents/planner.md`, `agents/worker.md`; updated AGENTS.md
  with role-switching rules.
- Demo pipeline (`src/demo_pipeline/`: data_gen → stats) and integration spec
  `configs/demo-pipeline.yaml` wired into CI.
- `make plan` / `make tasks` targets; `docs/orchestration.md` reference.
- `workflow_dispatch` triggers on both CI workflows.

## [0.1.0] - 2026-08-28

### Added

- Initial template: `harness` package (spec loading, runner, checks, reports,
  reproducibility utilities, CLI).
- Demo verification spec (`configs/demo.yaml`) and demo step script.
- Makefile targets: `setup`, `lint`, `format`, `test`, `verify`, `reproduce`, `clean`.
- CI workflows: lint + tests (Python 3.10–3.12), verification + determinism gate.
- Issue/PR templates, pre-commit config, docs (verification, reproducibility,
  architecture).
