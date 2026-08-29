# AGENTS.md

Ground rules for AI coding agents (and humans) working in repositories created
with Research Harness. Read this before making any change.

Lost? `python -m harness status` reads the repository's real state and names
the next action.

## Repository purpose

This is a research project with **two-tier agent orchestration**:
a Planner agent owns direction and flow (`plans/`), Worker agents each own one
module task (`tasks/`), and the harness enforces every contract
machine-checkably. Project-specific research code lives alongside it and must
keep using the harness for anything that needs to be trusted or reproduced.

## Orchestration roles

Work happens in three tiers. The researcher (Tier 1) sets direction and
decides what gets merged; the Planner (Tier 2) owns one experiment end to end;
Workers (Tier 3) each implement one module. **Never merge an experiment branch
yourself** — reporting is the Planner's job, merging is the researcher's.

You are always acting in ONE of three roles — know which:

- **Planner** ([agents/planner.md](agents/planner.md)): own `plans/*.yaml`,
  module DAGs, contracts, acceptance, the integration spec, and the experiment
  `report:` the researcher asked for. Work inside your experiment's worktree.
  Never write module code. Hand off via `harness plan materialize`; hand back
  via `harness exp report`.
- **Worker** ([agents/worker.md](agents/worker.md)): claim exactly one task
  (`harness task claim`), implement it fully against the task file's brief
  and contract, verify (`harness task verify`), mark done (`harness task
  done`). Never touch other modules, the plan, or `harness/`.
- **Maintainer** (default): work on project code, CI, or docs. Follow
  the rules below.

If a Worker finds a contract ambiguous or a dependency broken: `harness task
block --reason "..."` and hand back to the Planner. Never improvise the plan.

The harness enforces the handoff rather than trusting it: `task claim` refuses
a task with unfinished dependencies, `task verify`/`done` fail when a declared
deliverable is missing, and `plan materialize --force` refreshes a task's spec
without erasing its status, worker, or log.

## Non-negotiable rules

1. **Verify before you finish.** A task is not done until
   acceptance tests and integration verification pass. If your change touches
   determinism (seeds, data loading, model code), run `harness reproduce` —
   it runs the spec twice and diffs every artifact, so a divergence fails
   rather than passing quietly.
2. **Never commit artifacts.** `data/` and `results/` are gitignored.
   Never commit checkpoints, logs, or datasets.
3. **Determinism first.** Every source of randomness must be seeded — via the
   spec's `seed`, the harness env vars, or explicit config. No unseeded
   `random`/`numpy`/`torch` calls in committed code paths. Every report
   records its provenance (commit, interpreter, platform, seed); a run made
   from a dirty worktree is flagged and is not a citable result.
4. **Declarative verification.** Prefer adding a check to a spec
   (`configs/*.yaml`) over writing one-off validation scripts. New behavior
   that produces outputs should come with new checks.

## Standard commands

Orchestration commands:

```bash
python -m harness plan validate|materialize|status plans/<plan>.yaml
python -m harness plan check                              # every plan, no name needed
python -m harness plan status plans/<plan>.yaml --check   # one plan, with drift
python -m harness task list|show|claim|block|verify|done --id <id>
python -m harness task verify --all [--status done]       # audit the board
python -m harness task run --id <id>                      # invoke a Worker + verify
python -m harness plan run plans/<plan>.yaml              # drain the ready queue
```

Experiment commands (Tier 1 boundary):

```bash
python -m harness exp start <name> [--question "..."] [--base main]
python -m harness exp question <name> [--set "..."]       # record it later
python -m harness exp list
python -m harness exp report <name> [--determinism] [--save]
python -m harness exp remove <name>
python -m harness planner brief <name> --register <label> # become a Planner
```

Workers are invoked through `configs/agents.yaml`. The default adapter writes a
briefing for a human; set `adapter: cli` to point at a headless coding agent.
The harness names no vendor — the command is configuration.

Verification commands:

```bash
python -m harness verify --spec configs/<spec>.yaml [--results-dir DIR]
python -m harness reproduce --spec configs/<spec>.yaml [--times N]
python -m harness hash <file>          # sha256 helper
```

## Where things live

- `agents/` — role contracts (planner.md, worker.md). Read the one for your role.
- `.experiments/` — experiment worktrees (gitignored; one per hypothesis).
- `plans/` — orchestration plans: goal, module DAG, contracts, briefs, acceptance.
- `tasks/` — materialized work orders with lifecycle state (status/worker/log). Committed.
- `configs/` — specs (`name`, `seed`, `steps`) and experiment configs. Data, not logic.
- `scripts/` — runnable steps referenced by specs.
- `results/runs/<spec>-<timestamp>/` — reports (`report.json`, `report.md`, `logs/`). Gitignored.

## How verification works

A spec is an ordered list of steps; each step runs a shell command and is
followed by checks. The runner exports these env vars to every step and
expands `${VAR}` in check `path` params:

- `HARNESS_RESULTS_DIR` — directory for this run's artifacts
- `HARNESS_RUN_ID` — spec name
- `HARNESS_PYTHON` — the interpreter running the harness
- `HARNESS_SEED` — the spec's `seed` (unset if none declared)

Write steps as `${HARNESS_PYTHON} script.py --seed ${HARNESS_SEED}`. A bare
`python` breaks on machines that ship only `python3`, and a hardcoded seed
silently drifts from the spec's `seed:`.
