# AGENTS.md

Ground rules for AI coding agents (and humans) working in repositories created
with Research Harness. Read this before making any change.

Lost? `python -m harness status` reads the repository's real state and names
the next action.

## Repository purpose

This is a research project with **two-tier agent orchestration**:
a Planner agent owns direction and flow (`.harness/plans/` or `plans/`), Worker agents each own one
module task (`.harness/tasks/` or `tasks/`), and the harness enforces every contract
machine-checkably. Project-specific research code lives alongside it and must
keep using the harness for anything that needs to be trusted or reproduced.

## Orchestration roles

Work happens in three tiers. The researcher (Tier 1) sets direction and
decides what gets merged; the Planner (Tier 2) owns one experiment end to end;
Workers (Tier 3) each implement one module. **Never merge an experiment branch
yourself** — reporting is the Planner's job, merging is the researcher's.

You are always acting in ONE of three roles — know which:

- **Planner** (agents/planner.md): own plans, module DAGs, contracts, acceptance, the integration spec, and the experiment report. Work inside your experiment's worktree. Never write module code.
- **Worker** (agents/worker.md): claim exactly one task, implement it fully against the task file's brief and contract, verify, mark done. Never touch other modules, the plan, or `harness/`.
- **Maintainer** (default): work on project code, CI, or docs. Follow the rules below.

## Non-negotiable rules

1. **Verify before you finish.** A task is not done until
   acceptance tests and integration verification pass. If your change touches
   determinism (seeds, data loading, model code), run `harness reproduce`.
2. **Never commit artifacts.** `data/` and `results/` are gitignored.
   Never commit checkpoints, logs, or datasets.
3. **Determinism first.** Every source of randomness must be seeded.
4. **Declarative verification.** Prefer adding a check to a spec over writing one-off validation scripts.

## Standard commands

Orchestration commands:

```bash
python -m harness plan validate|materialize|status <plan>.yaml
python -m harness plan check                              # every plan, no name needed
python -m harness task list|show|claim|block|verify|done --id <id>
python -m harness task verify --all [--status done]       # audit the board
python -m harness task run --id <id>                      # invoke a Worker + verify
python -m harness plan run <plan>.yaml                    # drain the ready queue
```

Experiment commands (Tier 1 boundary):

```bash
python -m harness exp start <name> [--question "..."] [--base main]
python -m harness exp question <name> [--set "..."]       # record it later
python -m harness exp list
python -m harness exp report <name> [--determinism] [--save]
python -m harness exp remove <name>
```

Verification commands:

```bash
python -m harness verify --spec <spec>.yaml [--results-dir DIR]
python -m harness reproduce --spec <spec>.yaml [--times N]
python -m harness hash <file>          # sha256 helper
```
