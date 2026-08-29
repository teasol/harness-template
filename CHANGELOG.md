# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Progress was counted across every task file, not the plan's own modules.**
  A new project inherits the shipped demo's *finished* board, so `plan status`
  and `exp report` reported "2/2 done" with none of the plan's modules built —
  and could have declared an experiment READY TO MERGE on that basis. Both now
  count only the plan's modules, list unmaterialized ones, and name foreign
  task files as ignored. Found by walking through a fresh project end to end.
- `exp report` could print `NOT READY` with no reason given. Every blocker is
  now stated, in the terminal and under `Why not ready` in the report.
- `exp start` scaffolded a plan pointing at an integration spec it did not
  create, so the Planner's first error was a missing file rather than the TODOs
  it had to fill in. The spec is now scaffolded too.
- `plan validate` accepted an untouched scaffold as a valid plan. A plan still
  carrying the scaffold marker is refused with an explanation.

### Added

- `configs/worker.yaml` now ships **working** commands for several coding
  agents, with flags checked against installed versions rather than guessed.
  The cli adapter always could spawn Workers, but no runnable example was
  provided, so in practice nobody could turn it on.
- `harness status` and `plan run` say when the adapter is `manual` — that no
  Workers will be spawned and how to change it — instead of letting a Planner
  wonder why nothing was built.
- The briefing reaches a Worker command as a file (`{brief_file}`) as well as
  on stdin, so agents that take a prompt argument work too. Both paths tested.

- **`harness status`** — reads the repository's real state (template or
  project, experiments in flight, each one's stage) and names the next command.
  It covers every stage: not yet instantiated, no experiments, plan still a
  scaffold, tasks not materialized, modules building, a worker blocked, ready
  to report. A newcomer never has to know where they are. Also `make status`.
- A **getting-started walkthrough at the top of `README.md`** for a researcher
  who has never seen this repository: question in, merge decision out.

- **Worker adapters (Tier 2 → Tier 3).** `harness task run --id <id>` invokes a
  Worker, verifies acceptance *and* deliverables, and retries with the real
  failure output — failing checks plus step logs — until an attempt cap
  (default 6). Retrying the same worker beats restarting: a coding agent handed
  its own failing test usually fixes it. On exhaustion the task is `blocked`
  with the reason logged, returning control to the Planner.
- `harness plan run <plan>` drains the ready queue in dependency order, so the
  Planner chooses *what* to run while the harness owns the loop.
- Two adapters, configured in `configs/worker.yaml`: `manual` (default — write
  a briefing for a human; no API key, works out of the box) and `cli` (run a
  headless coding agent). The command is the lab's configuration; the harness
  names no vendor and ships no tool-specific flags.
- Worker briefings are assembled from the task: brief, contract, deliverables,
  constraints, and the exact acceptance commands that will judge the work.
- **`harness planner brief <name> [--register <label>]`** — everything a session
  needs to act as an experiment's Planner, as plain text any runtime can follow.
  `--register` records who is driving an experiment.
- `integrations/` — optional tool-specific shims. Nothing there is required;
  the harness is driven entirely by `python -m harness ...`.
- `make run`.

### Notes

- Cost is never estimated. The harness records attempts, durations, exit codes,
  and the configured adapter, and reports `cost: not measured` when an adapter
  supplies none — the same rule that stops an agent narrating an unmeasured
  result.

- **Experiments (Tier 1 ↔ Tier 2).** `harness exp start|list|report|remove`.
  Each experiment is one hypothesis on its own branch in its own git worktree,
  so several run side by side. `exp remove` keeps the branch — a rejected
  experiment stays inspectable. Workers remain sequential within an
  experiment, which keeps the task board coherent and dependency gates correct.
- **Experiment reports.** `exp report` measures the spine itself (integration
  result, per-task acceptance re-verification, determinism, the commit to
  merge, and an explicit *Not verified* list) and extracts the metrics the
  researcher asked for from real run artifacts. Exits non-zero unless
  merge-ready. `--save` writes the report into the branch. The harness never
  merges: that decision stays with the researcher.
- **`report:` section in plans.** The researcher states what they want to see;
  the Planner declares *where* each number lives; the harness supplies the
  value. An agent can no longer report a result it was not made to measure.
- Report `source`/`artifacts` paths must stay inside the experiment (no
  absolute paths, no `..`), so every report can be judged on its own terms.
  Cross-experiment comparison belongs to the researcher.
- `plan validate` rejects a deliverable claimed by more than one module.
- `make experiments`; `docs/experiments.md`.
- `harness plan check` — validates every plan in `plans/` and flags drift
  without naming one, so the Makefile, pre-commit, and CI keep gating a project
  whose plans have changed. `make drift`, the pre-commit hook, and CI now use it.
- `harness task list --plan <name>`, plus a PLAN column, so a board holding
  more than one plan's tasks is legible.
- `scripts/instantiate.py` now **always** removes the shipped orchestration
  example: a project should not begin holding someone else's finished task
  board, so it is not a choice. `--exam-demo` runs that example end to end
  (plan → board → acceptance → integration → determinism) so the flow can be
  seen on real output before it goes. The one-step smoke test is kept, so
  `make verify` still works on day one.
- `Makefile` gained `SPEC` and `PLAN` variables; a project points them at its
  own files instead of editing targets.

### Fixed

- Provenance reported `git_dirty: null` for a *clean* worktree, conflating
  "no changes" with "git unavailable" — empty `git status` output was being
  treated as failure.
- Steps now run with the verified tree at the front of `PYTHONPATH`. An
  editable install points at one checkout, so a step inside an experiment
  worktree imported the **main** checkout's code and silently verified the
  wrong source.

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
