# Getting started

For a researcher who has never seen this repository. It assumes you know
Python and git, and nothing about this template.

If you only remember one command, remember this one:

```bash
python -m harness status
```

It reads the repository's actual state and tells you what to do next. It works
at every point below, so you never have to remember where you are.

## What this is, in one paragraph

You state a research question. An AI **Planner** takes it, breaks it into
modules, hands each to an AI **Worker**, and verifies the result. Every
experiment lives on its own git branch. When one finishes you get a report —
integration passed or not, which modules were built, whether the results
reproduce, the numbers you asked for — and **you** decide whether to merge it.
The harness measures; it never decides.

## 0. Make it your project

```bash
python3 scripts/instantiate.py --name my-project --drop-demo
git add -A && git commit -m "chore: instantiate from harness-template"
make setup
make verify && make test
```

`--drop-demo` removes the shipped example. Skip it if you want a worked
example to read first — but then your task board starts out holding the demo's
finished tasks, which can be confusing.

`make verify` runs a one-step spec end to end. If it passes, the harness works
on your machine.

## 1. Start an experiment

An experiment is **one question**. Give it a short name:

```bash
python -m harness exp start sparse-attention
```

That creates a branch `exp/sparse-attention` and a separate working directory
(a git *worktree*) under `.experiments/`. Several experiments can exist at once
without disturbing each other or your main checkout.

It also writes a plan skeleton full of `TODO`s. The skeleton is not a plan —
`plan validate` refuses it until the TODOs are gone.

## 2. Hand it to a Planner

Open an agent session — whatever coding agent you use — and give it this:

```bash
python -m harness planner brief sparse-attention --register session-01
```

Run that command, paste its output into the session, and tell the session to
follow it. From then on that session is the Planner for this experiment: it
knows which worktree it owns, what the rules are, and what to do next.

**Tell it your question and what you want reported.** For example:

> Does keeping only the top 10% of attention weights preserve most of the
> attention mass? Report the retained mass and the fraction kept.

The Planner records *where each number will come from* in the plan. It never
writes a value — the harness extracts values from real run artifacts, so a
number in your report was measured, not asserted.

## 3. Let the Planner work

The Planner writes `plans/<name>.yaml`, then:

```bash
python -m harness plan validate plans/sparse-attention.yaml
python -m harness plan materialize plans/sparse-attention.yaml   # one task per module
python -m harness plan run plans/sparse-attention.yaml           # Workers build them
```

`plan run` hands each module to a Worker, checks the result, and retries with
the actual failure output — up to 6 attempts. If a module still fails, it is
marked `blocked` and handed back to the Planner. That usually means the brief
or the contract is wrong, not that the Worker is bad.

By default Workers are **manual**: the harness writes a briefing file for you
to paste into another agent session. To automate, point
`configs/worker.yaml` at your coding agent's headless mode.

## 4. Read the report and decide

```bash
python -m harness exp report sparse-attention --determinism --save
```

You get something like:

```
[sparse-attention] READY TO MERGE
  integration: PASSED
  tasks:       2/2 done
  determinism: REPRODUCIBLE
  commit:      1ebd0bed...
  retained_mass: 0.198128
```

`READY TO MERGE` means the harness could not find anything wrong — not that
the result is interesting. **That judgement is yours.** If it says `NOT READY`
it lists exactly why.

Then, if you want it:

```bash
git merge exp/sparse-attention
```

Nothing else merges for you. If you decide against the experiment, just don't
merge — the branch stays, so the attempt remains on the record.

## Running several experiments

Start as many as you like; each gets its own branch and directory.

```bash
python -m harness exp start baseline
python -m harness exp start sparse-attention
python -m harness exp list
```

Each report stands on its own — an experiment may not read another's results.
Comparing them is **your** job, done by reading the finished reports. That is
deliberate: a result that only makes sense next to another one cannot be
judged, and the harness will refuse a plan that tries.

## When something goes wrong

| Symptom | What it means |
| --- | --- |
| `plan validate` says "still the scaffold" | The TODOs have not been filled in yet. |
| A task is `blocked` | A Worker used up its attempts. Read the task log; usually the brief is ambiguous. |
| `exp report` says `NOT READY` | It lists every blocker. Fix them, or decide the experiment failed — that is a valid outcome. |
| `NOT REPRODUCIBLE` | Something is unseeded. See [reproducibility.md](reproducibility.md). |
| Report says `not measured` for cost | Expected. The harness does not estimate token spend. |

## Where to go next

- [experiments.md](experiments.md) — worktrees, reports, merge readiness
- [orchestration.md](orchestration.md) — plans, tasks, contracts, Workers
- [verification.md](verification.md) — specs and checks
- [reproducibility.md](reproducibility.md) — determinism
- [../AGENTS.md](../AGENTS.md) — the rules agents work under
