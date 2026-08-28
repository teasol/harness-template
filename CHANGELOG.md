# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
