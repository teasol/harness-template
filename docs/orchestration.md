# Orchestration reference

This template implements a **two-tier agent harness**: a Planner agent owns
the direction and the module DAG; Worker agents each own one module, built in
isolation against a self-contained spec. The harness is the contract between
them — everything machine-checkable is enforced, everything else is written
down.

```mermaid
flowchart TD
    P["Planner agent<br/>(agents/planner.md)"] -->|"writes"| PL["plans/*.yaml<br/>goal · DAG · contracts · briefs · acceptance"]
    PL -->|"harness plan materialize"| T["tasks/*.task.yaml<br/>self-contained work orders"]
    T -->|"harness task claim"| W1["Worker A<br/>(agents/worker.md)"]
    T -->|"harness task claim"| W2["Worker B"]
    W1 -->|"implements"| S1["src/... deliverables"]
    W2 -->|"implements"| S2["src/... deliverables"]
    S1 -->|"harness task verify/done"| H["Runner + checks<br/>(existing harness)"]
    S2 --> H
    H -->|"status/log"| T
    T -->|"all done"| I["integration spec<br/>(configs/*.yaml)"]
    I --> H
```

## Lifecycle

```mermaid
stateDiagram-v2
    [*] --> todo: plan materialize
    todo --> in_progress: task claim (--by worker)
    blocked --> in_progress: task claim
    in_progress --> done: task done (acceptance passes)
    in_progress --> blocked: task block (--reason)
    done --> [*]
```

Task state lives inside each task file (`status`, `worker`, `log`), so agents
coordinate through ordinary git commits.

## Commands

| Command | Role | Purpose |
| --- | --- | --- |
| `harness plan validate <plan>` | Planner | Check schema, DAG (cycles, unknown deps), acceptance check types |
| `harness plan materialize <plan>` | Planner | Write `tasks/<id>.task.yaml` per module (skips existing; `--force` overwrites) |
| `harness plan status <plan>` | Planner | Module progress + integration pointer |
| `harness task list [--status S]` | both | The board; `READY=yes` = todo + all deps done |
| `harness task show --id <id>` | Worker | Print the full work order |
| `harness task claim --id <id> --by <name>` | Worker | todo/blocked → in_progress |
| `harness task block --id <id> --reason "..."` | Worker | Park a task with a reason for the Planner |
| `harness task verify --id <id>` | Worker | Run the task's acceptance steps |
| `harness task done --id <id> [--by <name>]` | Worker | Verify, then mark done (fails if acceptance fails) |
| `harness verify --spec <spec>` | Planner | Integration check of the assembled whole |

## Plan schema (`plans/*.yaml`)

```yaml
plan:
  name: my-plan            # required
  goal: >                  # required, one paragraph
    ...
  description: ...         # optional
  integration:             # optional: spec run after ALL modules are done
    spec: configs/my-plan.yaml
  modules:                 # required, non-empty; forms a DAG
    - id: module-a         # unique
      title: ...           # human label
      depends_on: []       # ids of modules whose outputs this consumes
      deliverables: [src/x/a.py]
      contract:
        inputs:  [{name: seed, type: int, description: ...}]
        outputs: [{name: dataset, type: path, description: ...}]
      brief: |             # the Worker's complete instructions
        ...
      constraints: [...]   # hard rules for the Worker
      acceptance:          # mini verification spec (same schema as configs/)
        steps:
          - id: check-a
            run: <shell command>
            checks: [{type: file_exists, path: ...}]
```

Validation enforces: unique ids, resolvable `depends_on`, acyclic DAG,
non-empty brief and acceptance per module, known check types, and (if set) an
existing integration spec file.

## Task schema (`tasks/*.task.yaml`)

Materialized from a plan — a Worker needs this file and nothing else:

```yaml
task:
  id: module-a
  plan: my-plan
  title: ...
  depends_on: []
  brief: |            # copied from the plan module
  contract: {...}     # copied
  deliverables: [...]
  constraints: [...]
  acceptance: {steps: [...]}
  status: todo        # todo | in_progress | done | blocked
  worker: null        # set on claim
  log: []             # append-only audit trail (timestamps + events)
```

Task files are machine-managed (`harness task ...` rewrites them); keep them
comment-free so round-trips stay clean.

## Design rules

- **Acceptance reuses the verification harness.** A task's acceptance is a
  standard spec (steps + checks) executed by the same Runner — one engine
  everywhere.
- **Acceptance must be self-contained.** It may invoke dependency CLIs to
  prepare its inputs; it must never depend on another task having run first
  in the same session.
- **Contracts are the only coupling.** Workers consume dependencies through
  declared interfaces (CLI, file formats), never through imports of each
  other's internals.
- **The board lives in git.** Status and log are plain YAML in task files;
  agents commit them alongside their code.

## Adding a module to a running plan

1. Planner edits `plans/<plan>.yaml` (append the module).
2. `harness plan validate` → `harness plan materialize` (writes only the new
   task file; existing tasks untouched).
3. A Worker claims the new task like any other.
