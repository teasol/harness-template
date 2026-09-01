---
name: Plan
about: Track one plan — its goal, what proves it, and where the report landed
labels: plan
---

## Goal

<!-- What this plan is for, in the words you would use out loud. The same text
     as the plan's `goal:`. -->

## The plan

- Plan file: `plans/<plan-name>.yaml`
- Planner:
- Modules, and who builds each (`executor: main` = the Planner, `sub` = delegated):

## What would prove it

- Integration spec: `configs/<name>.yaml`
- Numbers of record — the plan's `report:` entries, and where each one lives:

## Result

<!-- Link the report the harness generated. Do not retype its numbers here: the
     harness extracts them from the artifacts, and a hand-copied metric is a
     number nobody measured. -->

- Report: `results/plans/<plan-name>/report.md`
- Determinism (`python -m harness report <plan-name> --determinism`):
- Output hashes (`python -m harness hash ...`):

## Verdict

<!-- What the report says, and what you decided: merge it, drop it, or what to
     change and try next. That call is yours, not the Planner's. -->
