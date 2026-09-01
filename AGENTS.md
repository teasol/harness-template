# AGENTS.md

Ground rules for AI coding agents (and humans) working in repositories created
with Research Harness. Read this before making any change.

Lost? `python -m harness status` reads the repository's real state and names
the next action. Picking up someone else's work — or your own from another
machine — start at `HANDOFF.md`: it is regenerated whenever the work moves and
carries what no file records, the decisions made and the dead ends already hit.

## Repository purpose

This is a research project with **two-tier agent orchestration**:
a Planner agent owns direction and flow (`.harness/plans/` or `plans/`), Worker agents each own one
module task (`.harness/tasks/` or `tasks/`), and the harness enforces every contract
machine-checkably. Project-specific research code lives alongside it and must
keep using the harness for anything that needs to be trusted or reproduced.

## Orchestration roles

Work happens in **two tiers**.

**Tier 1 — research strategy and decision.** The user and the Planner
talk: what is being asked, what would count as an answer, what gets reported.
The user decides what gets merged. **Never merge a plan's branch
yourself** — reporting is the Planner's job, merging is the user's.

**Tier 2 — serial execution**, one dedicated plan per piece of work. The Planner
is also the **Main Worker**, and it does the building: it works through the
plan's modules in order, and `executor` defaults to `main`, so every module is
its own unless it says otherwise. Where a module is routine bulk — long
mechanical coding, log parsing — it marks that one `executor: sub`, a
**Sub-Worker** is spawned for it alone, finishes it, and returns; the Main Worker
carries on with the next module. One Planner runs **many** plans; each plan has
one Planner. The harness verifies whatever comes out, whoever produced it.

You are always acting in ONE of these roles — know which:

- **Planner / Main Worker** (agents/planner.md): own the plans, module DAGs, contracts, acceptance, the integration spec, and the report — **and build the modules.** That is the default: every module is yours unless you mark it `executor: sub`, which you do for routine bulk a brief can specify completely. Choosing what to hand off is your judgement, and it is the judgement the tier exists for.
- **Sub-Worker** (agents/worker.md): claim exactly one task, implement it fully against the task file's brief and contract, verify, mark done. Never touch other modules, the plan, or `harness/` — **this is enforced, not merely asked**: the harness hashes its own package around every invocation and fails the task outright if it changed, and fails it too if you modified a tracked file you never declared as a deliverable. If acceptance fails for an infrastructural reason, say so in your output and stop; do not fix the harness.
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
python -m harness plan validate|materialize|status <plan>
python -m harness plan approve <plan> --by <who>           # plan run requires this
python -m harness plan check                              # every plan, no name needed
python -m harness task list|show|claim|block|verify|done --id <id>
python -m harness task verify --all [--status done]       # audit the board
python -m harness task run --id <id>                      # invoke a Worker + verify
python -m harness plan run <plan>                         # drain the ready queue
python -m harness progress [--watch]                      # where it is right now
```

Plan commands (Tier 1 boundary):

```bash
python -m harness project init|show                        # what a Planner must know here
python -m harness create -n <name> [--model M] [--effort E]  # a Planner
python -m harness planner set <name> --model M            # once a manual one is known
python -m harness planner list|show <name>
python -m harness planner note <name> --add "..." [--plan P]
python -m harness plan new <name> [--planner P] [--base main]
python -m harness plans
python -m harness report <name> [--determinism] [--save]
python -m harness plan drop <name>
```

Verification commands:

```bash
python -m harness verify --spec <spec>.yaml [--results-dir DIR]
python -m harness reproduce --spec <spec>.yaml [--times N]
python -m harness hash <file>          # sha256 helper
