# AGENTS.md

Ground rules for AI coding agents (and humans) working in repositories created
with Research Harness. Read this before making any change.

Lost? `python -m harness status` reads the repository's real state and names
the next action.

## Repository purpose

This is a research project with **two-tier agent orchestration**:
a Planner agent owns direction and flow (`.harness/plans/`), Worker agents each own one
module task (`.harness/tasks/`), and the harness enforces every contract
machine-checkably. Project-specific research code lives alongside it and must
keep using the harness for anything that needs to be trusted or reproduced.

## Orchestration roles

Work happens in **two tiers**.

**Tier 1 — research strategy and decision.** The researcher and the Planner
talk: what is being asked, what would count as an answer, what gets reported.
The researcher decides what gets merged. **Never merge an branch branch
yourself** — reporting is the Planner's job, merging is the researcher's.

**Tier 2 — serial execution**, one dedicated branch per piece of work. The
Planner is also the **Main Worker**: it does the core logic, planning and
orchestration itself, and delegates routine bulk — long mechanical coding, log
parsing — to a **Sub-Worker**, one at a time. One Planner runs **many**
branches; each branch has one Planner. The harness verifies whatever
comes out, whoever produced it.

You are always acting in ONE of these roles — know which:

- **Planner / Main Worker** (agents/planner.md): own the plans, module DAGs, contracts, acceptance, the integration spec, and the report. Implement directly when the work is core logic or orchestration; delegate when it is routine bulk. Choosing which is your judgement, and it is the judgement the tier exists for.
- **Sub-Worker** (agents/worker.md): claim exactly one task, implement it fully against the task file's brief and contract, verify, mark done. Never touch other modules, the plan, or `harness/` — **this is enforced, not merely asked**: the harness hashes its own package around every invocation and fails the task outright if it changed, and fails it too if you modified a tracked file you never declared as a deliverable. If acceptance fails for an infrastructural reason, say so in your output and stop; do not fix the harness.
- **Maintainer** (default): work on project code, CI, or docs. Follow the rules below.

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
   (`.harness/configs/*.yaml`) over writing one-off validation scripts. New behavior
   that produces outputs should come with new checks.

## Standard commands

Orchestration commands:

```bash
python -m harness plan validate|materialize|status .harness/plans/<plan>.yaml
python -m harness plan check                              # every plan, no name needed
python -m harness plan status .harness/plans/<plan>.yaml --check   # one plan, with drift
python -m harness task list|show|claim|block|verify|done --id <id>
python -m harness task verify --all [--status done]       # audit the board
python -m harness task run --id <id>                      # invoke a Worker + verify
python -m harness plan run .harness/plans/<plan>.yaml     # drain the ready queue
```

Branch commands (Tier 1 boundary):

```bash
python -m harness branch <name> [--question "..."] [--base main]
python -m  <name> [--set "..."]       # record it later
python -m harness branches
python -m harness report <name> [--determinism] [--save]
python -m harness drop <name>
python -m harness planner brief <name> --register <label> # become a Planner
```

Workers are invoked through `.harness/configs/agents.yaml`. The default adapter writes a
briefing for a human; set `adapter: cli` to point at a headless coding agent.
The harness names no vendor — the command is configuration.

Verification commands:

```bash
python -m harness verify --spec .harness/configs/<spec>.yaml [--results-dir DIR]
python -m harness reproduce --spec .harness/configs/<spec>.yaml [--times N]
python -m harness hash <file>          # sha256 helper
```

## Directory layout

```
.harness/
├── agents/             # role contracts (planner.md, worker.md)
├── configs/            # agent platform & tier configurations
├── plans/              # orchestration DAGs (<plan>.yaml)
└── tasks/              # worker task files (<module>.task.yaml)
.worktrees/           # worktrees for isolated branch attempts (ignored)
results/                # verification run logs and generated artifacts (ignored)
```
