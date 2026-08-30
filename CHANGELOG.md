# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.3] - 2026-08-30

### Added

- **`harness create` hands you something to paste when the Planner is manual.**
  Nobody spawns a manual Planner — a person opens that session — so the harness
  cannot brief it the way it briefs a configured one, and whoever just ran
  `create` had nothing to give it. It now prints a short block naming the
  Planner, the files to read (role contract, ground rules, `project.yaml` when
  present), how to record its model, and `harness status` for what to do next.
  Paths, not pasted contracts: those files are long, already authoritative, and
  a copy inside a prompt only drifts from them. Only files that exist are
  listed. Nothing is printed for a configured tier, where it would be noise.

## [0.3.2] - 2026-08-30

### Fixed

- **`harness create` refused to run under a manual Planner tier.** `--model`
  was required, so the one configuration where the model is genuinely
  unknowable — a manual Planner is a session a person opens later — could not
  create a Planner at all:

  ```
  harness create: error: the following arguments are required: --model
  ```

  The requirement was right, the enforcement point was wrong. Knowing the model
  still matters (two runs planned by different models are not the same
  experiment), but that is insisted on where it can be — the report, which
  already refuses to call such a run comparable. So `--model` is now optional:
  it defaults to whatever `harness setup` recorded for the Planner tier, and
  when nothing is known the Planner is created anyway and told plainly what the
  gap costs. **`harness planner set <name> --model <model>`** closes it once the
  session says what it is, without disturbing the Planner's notes or history.

## [0.3.1] - 2026-08-30

Everything here came from the first release meeting a real project: the harness
was pointed at an existing research codebase, drove a full reproduction end to
end, and each entry below fixes something that went wrong while doing it.

### Changed

- **Two tiers, not three.** Tier 1 is the researcher and the Planner settling
  the question and deciding the merge. Tier 2 is one experiment branch, where
  **the Planner is also the Main Worker**: it implements the core work itself
  and delegates routine bulk — long mechanical coding, log parsing — to a
  **Sub-Worker**, one at a time. And a Planner runs **many** experiments rather
  than being consumed by one.

  This was already half-true in the code (`executor:`, persistent Planners) but
  the documentation said the opposite, and the Planner's own role contract
  opened with "Never implement modules yourself" — the exact behaviour the
  model now requires. Agents read those contracts, so a contract that
  contradicts the model is worse than none.

  `executor:` values are now `main` (the Main Worker does it) and `sub`
  (delegate); `planner`/`worker` are still accepted, since they named the same
  two roles. Whichever is chosen, the module keeps its contract and its
  acceptance: what changes is who writes the code, never whether it is checked.

### Fixed

- **`harness init` is safe to re-run.** A project initialized by an older
  version could not catch up: `init` refused with "already initialized", and
  the only way forward was `--force`, which overwrites `agents.yaml` too —
  throwing away the platform, model and command a lab had configured in order
  to install a file that was merely missing. It is now additive by default,
  adds only what is absent, reports how many existing files it left alone, and
  keeps `--force` for a deliberate reset.
- **The Antigravity preset shipped an invocation that does not work.** `agy`
  takes the prompt as the value of `-p` while the harness delivers the briefing
  on stdin, so bare `-p` swallowed the next flag as its prompt and the agent
  never saw the task — six attempts failing in under a second each. The preset
  now uses `-p="$(cat)"`, which is the form `agy` accepts; `harness setup --check`
  catches the same class of mismatch for any platform.

### Changed

- **`harness create -n <name> --model <model>`.** A Planner is the only thing
  the harness creates, so it does not need a namespace to itself — the command
  now reads like `conda create -n`. `harness planner create` still works, since
  it shipped in 0.3.0, but everything that teaches the flow points at
  `harness create`.
- `harness init` no longer opens with the demo spec. Creating a Planner is the
  actual next step; the smoke test stays reachable on a following line rather
  than standing between you and starting work.

- **The Planner is registered before the experiment it owns, everywhere.**
  `planner create` shipped, but `init`, `status` and the README all still led
  straight to `exp start`, so the documented flow and the intended one had
  drifted apart. Ordering matters and is not cosmetic: an experiment started
  under a registered Planner inherits its model — without one the report says
  "model not recorded" and the run cannot be compared with any other — and its
  briefing opens with what that Planner already learned here. Registering
  afterwards works, but by then the first briefing has been written without any
  of it. `status` now offers `planner create` first and names the Planner once
  one exists, and `exp start` says plainly when the name it was given is only a
  label.

### Fixed

- **The quickstart did not work.** `harness init` followed by
  `harness verify --spec configs/demo.yaml` — the first two commands in the
  README — failed on every fresh project, for two reasons: `init` writes specs
  under `.harness/` so they cannot collide with a project's own `configs/`, but
  `--spec` never looked there (tasks and plans already resolved both ways), and
  the demo spec was shipped without the script it runs. Specs now fall back to
  the harness config directory, `init` installs the demo script, and the runner
  exports `${HARNESS_DIR}` so a shipped spec can find its own files without
  assuming a layout. Covered by a test that runs the quickstart verbatim.

### Added

- **`harness init` notices when it lands on an existing codebase.** Most
  projects do not start empty, and until now that looked identical to one that
  did — the same two next steps either way, so the fact that mattered on day
  one, that none of this code is covered by a contract or an acceptance check,
  went unsaid. Adoption is now recorded (the commit the harness arrived at and
  how many source files predate it, so "unverified" has a boundary rather than
  being a feeling), and `init` prints the order that actually works:
  `project init` → `planner create` → `exp start --planner`.

  Every Planner briefing then opens with that situation until some experiment
  here reaches a report. **No modularization procedure is prescribed, by
  design.** Deciding the decomposition is Tier 2's job, and a pipeline baked
  into the tool would both take that away and be wrong for the next project.
  What the briefing supplies is what generalizes: the five conditions a module
  boundary must satisfy — each one a consequence of something the harness can
  or cannot enforce — and the ordering principle that in research code the
  artifact of record is a *measurement*, so the numbers get pinned before
  anything moves.

### Fixed

- **`exp report --no-run` reused nothing.** It skipped the integration run and
  then reported every metric as "no integration run to read from", so producing
  a report meant paying for the whole integration again — hours of GPU in the
  case that found this. It now attaches the most recent run of the plan's own
  integration spec and names it, with the caveat that the numbers are not
  fresh. Merge-readiness no longer depends on string-matching the human status
  line: a reused run that passed counts as evidence, a reused run **from a
  different commit** is a blocker, because it passed for code that is not the
  code being reported on.
- **`worker.json` could contradict the board forever.** A task a Planner
  verified and finished by hand left the Worker record permanently claiming
  `failed` for a task the board called `done`, with nothing saying which to
  believe. `task done` now reconciles the record, preserving the Worker's
  attempt history and recording who corrected it and why.
- **`json_metric` rejected booleans with an unhelpful message.** It now names
  the problem and the fix ("emit 1 instead of true"), and points at
  `text_contains` for strings. `true` is still rejected rather than coerced:
  `equals: 1` and `equals: true` would otherwise be the same assertion, and a
  pass/fail flag that compares equal to a measurement is a bug waiting for a
  bad day.

### Added

- **A progress heartbeat, and `harness progress` to read it.** Long operations
  were opaque by construction: the step runner and the Worker adapter both call
  `subprocess.run(capture_output=True)`, which buffers everything until the
  child exits — so a Worker attempt running to a 30-minute cap produced no
  output, no elapsed time, and no way to tell a working agent from a wedged
  one. A plan is serial, so the position is always knowable; it is now
  published to `results/heartbeat.json` with a timestamp that keeps ticking
  while work is in flight.

  ```
  $ harness progress
  worker primary7-runner (module 1/2 · attempt 2/6) · running 12m30s · 17m30s before the cap
  ```

  `--watch` refreshes it. `harness status` shows what is running before
  anything else. A heartbeat whose ticker stopped is reported as **dead**
  rather than slow — the distinction you actually need during a long wait —
  and the blocked terminal now prints each attempt as it starts, with its cap,
  instead of only when it ends. Writing a heartbeat is best-effort: describing
  the work can never fail the work.

## [0.3.0] - 2026-08-29

First release shaped by real use: the harness was pointed at an existing
research project end to end, and every entry below fixes something that
went wrong doing it rather than something that looked wrong on paper.

> **Note on versions.** `0.2.0` shipped without bumping `pyproject.toml`
> or `harness.__version__`, both of which stayed at `0.1.0`. Provenance
> recorded during that period therefore reports `0.1.0` — treat a run
> claiming `0.1.0` as "0.1.0 or 0.2.0". A test now keeps the package
> version and `pyproject.toml` in agreement so this cannot recur.

### Security

- **A Worker can no longer modify the harness itself.** Observed in the field:
  an agent whose acceptance step failed for an infrastructural reason patched
  `runner.py` to make it pass. Acceptance run under a harness the agent just
  rewrote proves nothing, so this is now detected by hashing the package around
  every invocation, and it is fatal — the task blocks immediately rather than
  retrying. `AGENTS.md` had always forbidden this; it is now enforced.
- **A Worker that modifies tracked files it never declared as deliverables now
  fails the task.** An undeclared change is one nothing checks. New undeclared
  files are recorded but allowed. Configurable per tier via `guard: strict |
  warn | off` for labs that develop the harness alongside the project.

### Added

- **`harness project` — the project describes itself once, instead of every
  Planner rediscovering it.** `configs/project.yaml` registers the documents a
  Planner must read (with `docs.authority` naming the one that wins when two
  sources disagree), the house reporting format, the environment script, the
  project interpreter, and conventions a plan must respect. Every Planner
  briefing opens with it, `exp start` copies it into the new worktree, and
  `project show` fails when a declared path does not exist. Motivated by a near
  miss: a summary line reading `SMAD4 0.4282 -> 0.5483` is a single-branch
  figure, while the authoritative per-task table said the arm scored `0.4465`.
- **`${PROJECT_PYTHON}`** is exported to every step when the project declares an
  interpreter. `${HARNESS_PYTHON}` is the harness's own and generally has none
  of the project's dependencies; conflating them fails at acceptance time.
- **Planners that outlive one experiment.** `planner create|list|show|note`
  registers a named Planner with a model and an accumulating memory;
  `exp start --planner <name>` hangs an experiment off it, inheriting the model
  and opening the briefing with what that Planner already learned. The registry
  lives in the main repository, so every experiment under one Planner appends
  to the same memory rather than to a copy that dies with the branch. Notes
  carry an explicit staleness warning; durable policy belongs in
  `project.yaml`, which the researcher owns.
- **`plan approve`, and `plan run` requires it.** A plan is a proposal until a
  person agrees to it. Approval is fingerprinted against the plan's contents,
  so editing a plan lapses its approval. Approving prints what it commits you
  to — module list and the worst-case agent time, which was previously
  invisible (two modules at six attempts and a 30-minute cap is a six-hour
  ceiling nobody could see). `--skip-approval` exists for automation.
- **`executor: planner` on a plan module.** Work that runs an experiment or
  reads a log does not benefit from Worker isolation, and briefing an agent to
  do it costs more than doing it. Such modules are never handed to a Worker;
  `plan run` skips them and names them as the Planner's own.
- **`deterministic_math: true` on a spec** — see *Changed* below.
- **`harness setup --check`** smoke-tests each configured tier with one cheap
  prompt and reports whether the agent actually received it. A platform preset
  that does not match the installed CLI used to fail silently: six attempts in
  under a second each, with the agent never having seen the task.
- **The retry loop stops when it stops making progress.** Three consecutive
  attempts that leave every deliverable byte-identical hand back to the Planner
  instead of spending the remaining cap repeating the same failure. An agent
  invocation that exits non-zero in under five seconds is reported as a worker
  *configuration* problem and not retried at all.

### Changed

- **`seed:` no longer changes your numbers.** It used to inject
  `CUBLAS_WORKSPACE_CONFIG=:4096:8` alongside `PYTHONHASHSEED`, which
  constrains cuBLAS algorithm selection — so declaring a seed silently
  redefined the quantity being measured, and a reproduction could drift from
  its own reference for a reason nobody declared. Deterministic math is now
  opt-in via `deterministic_math: true`. Every variable the harness injects is
  recorded in provenance and printed in the report.
  **Migration:** specs that relied on the old behaviour must add
  `deterministic_math: true`.
- Attempt logs record the command that actually ran, placeholders resolved,
  instead of the raw template. The old behaviour printed `{model}` literally
  and always showed `resume_command`, which reads as a substitution bug during
  exactly the debugging session it was meant to help.
- A timed-out attempt now writes an attempt log with whatever the process
  emitted. The most expensive attempt in a run used to be the only one that
  left no record at all.
- Acceptance failures say *why*: the failing step id and the tail of its log
  travel with the verdict instead of only `acceptance failed`.
- `plan run` now explains why each remaining module is not running — blocked,
  waiting on a dependency, claimed, or the Planner's own — instead of
  `no ready tasks left`.
- `exp start` copies the project's agent configuration into the new worktree.
  Without it the worktree found no `agents.yaml` and fell back to the manual
  adapter silently, so `plan run` wrote briefings and spawned nothing while
  looking like it had succeeded. `harness status` now distinguishes "manual by
  choice" from "manual because nothing is configured".
- Registering a Planner by hand requires `--model`. A run whose Planner model
  is unknown cannot be compared with any other run; when it is missing, the
  report says so as a stated caveat and the briefing opens by asking the
  session to register itself.

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

### Changed

- **`exp start` now creates and briefs the Planner in one step.** One
  experiment has exactly one Planner, so activating it separately was a step
  that existed for no reason. Starting an experiment registers its Planner and
  prints the briefing.
- **The briefing has one fixed shape.** It previously took five forms depending
  on whether a question existed and what state the plan was in — one command
  meaning several different things. Now it is always Question / State / Next /
  Your role / The whole sequence, with only the contents varying, and its state
  logic is the same `experiment_state` that `harness status` uses rather than a
  second copy of it.
- `Next` in the briefing and in `status` always names a real action. For an
  unwritten or scaffold plan it used to point back at the briefing itself.
- "question unsettled" is now the first experiment state, so `harness status`
  shows it too.

### Added

- **`harness exp question <name> [--set "..."]`** — the research question no
  longer has to exist before the experiment does. A question usually gets
  sharper by talking it through, so the normal path is now: open the
  experiment, activate a Planner, agree on what is actually being asked, and
  record it when it settles. With none recorded, the Planner's briefing says so
  and instructs it to establish the question with the researcher before
  planning or spawning any Worker. `planner run` still refuses without one,
  since a spawned Planner has nobody to ask and would otherwise invent a goal.

- **`harness exp start --question "..."`** — the research question, verbatim,
  stored with the experiment and placed at the top of the Planner's briefing.
  Without it a spawned Planner had no way to learn what was being asked: the
  question lived in the plan's `report.question`, which is the very thing the
  Planner has not written yet.

### Fixed

- Agent presets ran with permissions that allow edits but not execution, so a
  Planner wrote a complete, valid plan and then stopped to ask permission to
  run `plan materialize`. An unattended agent must be able to run the commands
  it is judged by. Found on the first live run, and now covered by a test.

- **Both tiers are configured, and both can be spawned.** `harness setup` now
  sets up the Planner *and* the Worker — platform, model, reasoning level, and
  retry cap each — writing `configs/agents.yaml` with the two side by side.
  Flags are `--planner-*` and `--worker-*`; with none given it is interactive.
- **`harness planner run <experiment>`** spawns a Planner and drives the
  experiment until it is *ready to report* or the cap is reached. A Worker's
  definition of done is its acceptance; a Planner's is the experiment. Lets a
  Tier 1 agent create Planners instead of a person opening each session.
- `--planner-session` / `--worker-session` attach a tier to an already-open
  session instead of starting a fresh one; presets carry the resume command for
  each platform. `planner brief --session <id>` records it.
- `configs/worker.yaml` is replaced by `configs/agents.yaml` (both tiers), and
  `configs/worker-platforms.yaml` by `configs/agent-platforms.yaml`. The old
  worker file is still read if present.
- `harness status` shows how both tiers are configured, including an attached
  session.

- **`harness setup`** — chooses the platform, **model, and reasoning level**
  Workers run on, and writes `configs/worker.yaml`. Interactive, or fully
  non-interactive via `--platform/--model/--effort`; `--list` shows what is
  available. A tier that cannot be chosen is not a tier: without this, Workers
  ran at whatever a tool defaulted to and the Tier 2/3 split bought nothing.
- `configs/worker-platforms.yaml` — platform presets as **data**, with model
  and reasoning-level flags checked against real installations. Adding a tool
  or a local model is an entry there, not a change to `harness/`.
- Worker commands substitute `{model}` and `{effort}`. A command referencing
  either without a configured value is refused, so a Worker never silently runs
  at the platform default.
- Both tiers are recorded and reported. `planner brief --register` takes
  `--model`/`--effort` for the Planner session (the harness cannot set it, but
  it can record it); every Worker invocation writes its platform, model, and
  effort to the task log and worker report; `exp report` shows both under
  **Tiers**. `harness status` shows the configured Worker tier.

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
