"""Command-line interface for the harness.

Usage::

    python -m harness verify --spec configs/demo.yaml [--results-dir DIR]
    python -m harness reproduce --spec configs/demo.yaml [--times 2]
    python -m harness hash <file> [<file> ...]

    # Two-tier orchestration (Planner → tasks → Workers)
    python -m harness plan validate <plan.yaml>
    python -m harness plan materialize <plan.yaml> [--tasks-dir tasks] [--force]
    python -m harness plan status <plan.yaml> [--tasks-dir tasks] [--check]
    python -m harness task list [--status todo] [--tasks-dir tasks]
    python -m harness task show --id <id> [--tasks-dir tasks]
    python -m harness task claim --id <id> --by <agent> [--force] [--tasks-dir tasks]
    python -m harness task block --id <id> --reason "..." [--tasks-dir tasks]
    python -m harness task verify (--id <id> | --all) [--status S] [--tasks-dir tasks]
    python -m harness task done --id <id> [--by <agent>] [--tasks-dir tasks]

"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from harness import task as task_mod
from harness.plan import PlanError, load_plan
from harness.report import write_reports
from harness.reproduce import (
    ReproduceError,
    reproduce,
    write_reproduce_report,
)
from harness.reproducibility import file_sha256
from harness.runner import Runner
from harness.spec import SpecError, load_spec
from harness.task import TaskError


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    runner = Runner(root=args.root, results_dir=args.results_dir)
    result = runner.run(spec)
    json_path, md_path = write_reports(result)

    status = "PASSED" if result.success else "FAILED"
    print(f"[{spec.name}] {status} ({len(result.steps)} step(s))")
    for step in result.steps:
        mark = "ok" if step.success else "FAIL"
        exit_col = "timeout" if step.exit_code is None else step.exit_code
        print(f"  {mark:>4}  {step.step_id}  (exit={exit_col}, {step.duration_s:.2f}s)")
        for check in step.checks:
            if not check.passed:
                print(f"         check failed [{check.check_type}]: {check.detail}")
    print(f"  report: {md_path}")
    print(f"  json:   {json_path}")
    return 0 if result.success else 1


def cmd_reproduce(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        result = reproduce(spec, times=args.times, root=args.root, results_dir=args.results_dir)
    except ReproduceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = write_reproduce_report(result, args.results_dir)
    if result.reproducible:
        print(
            f"[{spec.name}] REPRODUCIBLE — {result.times} runs, "
            f"{len(result.manifest)} artifact(s) identical"
        )
        for rel, digest in result.manifest.items():
            print(f"    ok  {rel}  {digest[:16]}…")
    else:
        print(f"[{spec.name}] NOT REPRODUCIBLE — {result.times} runs", file=sys.stderr)
        for line in result.differences:
            print(f"  DIFF  {line}", file=sys.stderr)
    print(f"  report: {report}")
    return 0 if result.reproducible else 1


def cmd_hash(args: argparse.Namespace) -> int:
    for path in args.paths:
        try:
            digest = file_sha256(path)
        except OSError as exc:
            print(f"error: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        print(f"{digest}  {path}")
    return 0


# ---------------------------------------------------------------------------
# Orchestration: plans (Planner side)


def _print_step_failures(result) -> None:
    for step in result.steps:
        if step.success:
            continue
        print(f"  FAIL {step.step_id} (exit={step.exit_code})")
        for check in step.checks:
            if not check.passed:
                print(f"         check failed [{check.check_type}]: {check.detail}")


def cmd_plan_validate(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    order = " → ".join(plan.topological_order())
    integration = plan.integration or "(none)"
    print(f"[{plan.name}] valid — {len(plan.modules)} module(s)")
    print(f"  order:      {order}")
    print(f"  integration: {integration}")
    return 0


def cmd_plan_materialize(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.plan)
        written = task_mod.materialize(plan, args.tasks_dir, force=args.force)
    except (PlanError, TaskError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if written:
        for path in written:
            print(f"  wrote {path}")
    else:
        print("  all task files already exist (use --force to overwrite)")
    return 0


def cmd_plan_status(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    board = {t.id: t for t in task_mod.load_board(args.tasks_dir)}
    print(f"Plan: {plan.name}")
    print(f"Goal: {plan.goal.strip()}")
    print()
    print("  MODULE       STATUS        WORKER           DEPENDS ON")
    for module_id in plan.topological_order():
        task = board.get(module_id)
        status = task.status if task else "unmaterialized"
        worker = task.worker if task and task.worker else "-"
        deps = ", ".join(plan.module(module_id).depends_on) or "-"
        print(f"  {module_id:<12} {status:<13} {worker:<16} {deps}")
    done = sum(1 for t in board.values() if t.is_done)
    total = len(plan.modules)
    print()
    print(f"Progress: {done}/{total} done")
    if plan.integration:
        print(f"Integration spec: {plan.integration}")

    drift = task_mod.spec_drift(plan, args.tasks_dir)
    if drift:
        print()
        print("Drift — task files no longer match the plan:")
        for module_id, fields in drift.items():
            print(f"  {module_id}: {', '.join(fields)}")
        print("  Fix with: harness plan materialize <plan> --force")
    if args.check:
        return 1 if drift else 0
    return 0


# ---------------------------------------------------------------------------
# Orchestration: tasks (Worker side)


def cmd_task_list(args: argparse.Namespace) -> int:
    board = task_mod.load_board(args.tasks_dir)
    if args.status:
        board = [t for t in board if t.status == args.status]
    if not board:
        print("(no tasks)")
        return 0
    ready = set(task_mod.ready_task_ids(task_mod.load_board(args.tasks_dir)))
    print("  ID           STATUS        WORKER           DEPENDS ON        READY")
    for task in board:
        deps = ", ".join(task.depends_on) or "-"
        can_start = "yes" if task.id in ready else "-"
        print(
            f"  {task.id:<12} {task.status:<13} {str(task.worker or '-'):<16} "
            f"{deps:<17} {can_start}"
        )
    return 0


def cmd_task_show(args: argparse.Namespace) -> int:
    try:
        task = task_mod.load_task(args.tasks_dir, args.id)
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(Path(task.path).read_text(encoding="utf-8"))
    return 0


def cmd_task_claim(args: argparse.Namespace) -> int:
    try:
        task = task_mod.claim(args.tasks_dir, args.id, args.by, force=args.force)
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"task '{task.id}' claimed by {args.by} → in_progress")
    return 0


def cmd_task_block(args: argparse.Namespace) -> int:
    try:
        task = task_mod.block(args.tasks_dir, args.id, args.reason)
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"task '{task.id}' → blocked ({args.reason})")
    return 0


def cmd_task_verify(args: argparse.Namespace) -> int:
    if not args.all and not args.id:
        print("error: task verify requires --id <id> or --all", file=sys.stderr)
        return 2
    try:
        if args.all:
            tasks = task_mod.load_board(args.tasks_dir)
            if args.status:
                tasks = [t for t in tasks if t.status == args.status]
        else:
            tasks = [task_mod.load_task(args.tasks_dir, args.id)]
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not tasks:
        print("(no matching tasks)")
        return 0

    failed = 0
    for task in tasks:
        result = task_mod.verify_task(task, root=args.root, results_dir=args.results_dir)
        status = "PASSED" if result.success else "FAILED"
        print(f"[{result.spec_name}] {status} ({len(result.steps)} step(s))")
        _print_step_failures(result)
        print(f"  report: {Path(result.run_dir) / 'report.md'}")
        if not result.success:
            failed += 1
    if len(tasks) > 1:
        print(f"{len(tasks) - failed}/{len(tasks)} task(s) passed")
    return 1 if failed else 0


def cmd_task_done(args: argparse.Namespace) -> int:
    try:
        task, result = task_mod.complete(
            args.tasks_dir,
            args.id,
            worker=args.by,
            root=args.root,
            results_dir=args.results_dir,
        )
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not result.success:
        print(f"task '{args.id}' acceptance FAILED — status not changed", file=sys.stderr)
        _print_step_failures(result)
        return 1
    print(f"task '{task.id}' → done")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Agent-first verification harness for reproducible research.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify", help="Run a verification spec")
    verify.add_argument("--spec", required=True, help="Path to the spec YAML file")
    verify.add_argument(
        "--root", default=".", help="Root directory for relative paths (default: cwd)"
    )
    verify.add_argument("--results-dir", default="results", help="Base directory for run outputs")
    verify.set_defaults(func=cmd_verify)

    rerun = sub.add_parser("reproduce", help="Run a spec repeatedly and compare artifact hashes")
    rerun.add_argument("--spec", required=True, help="Path to the spec YAML file")
    rerun.add_argument("--times", type=int, default=2, help="How many runs to compare (>= 2)")
    rerun.add_argument("--root", default=".", help="Root directory for relative paths")
    rerun.add_argument(
        "--results-dir", default="results/reproduce", help="Base directory for run outputs"
    )
    rerun.set_defaults(func=cmd_reproduce)

    hash_cmd = sub.add_parser("hash", help="Print the sha256 of file(s)")
    hash_cmd.add_argument("paths", nargs="+", help="File path(s)")
    hash_cmd.set_defaults(func=cmd_hash)

    plan_cmd = sub.add_parser("plan", help="Plan (orchestration) commands")
    plan_sub = plan_cmd.add_subparsers(dest="plan_command", required=True)

    plan_validate = plan_sub.add_parser("validate", help="Validate a plan file")
    plan_validate.add_argument("plan", help="Path to the plan YAML file")
    plan_validate.set_defaults(func=cmd_plan_validate)

    plan_materialize = plan_sub.add_parser("materialize", help="Write task files from a plan")
    plan_materialize.add_argument("plan", help="Path to the plan YAML file")
    plan_materialize.add_argument("--tasks-dir", default="tasks")
    plan_materialize.add_argument(
        "--force", action="store_true", help="Overwrite existing task files"
    )
    plan_materialize.set_defaults(func=cmd_plan_materialize)

    plan_status = plan_sub.add_parser("status", help="Show module progress for a plan")
    plan_status.add_argument("plan", help="Path to the plan YAML file")
    plan_status.add_argument("--tasks-dir", default="tasks")
    plan_status.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if task files have drifted from the plan",
    )
    plan_status.set_defaults(func=cmd_plan_status)

    task_cmd = sub.add_parser("task", help="Worker task commands")
    task_sub = task_cmd.add_subparsers(dest="task_command", required=True)

    for name, help_text, func in [
        ("list", "List tasks on the board", cmd_task_list),
        ("show", "Print a task file", cmd_task_show),
        ("claim", "Claim a task (todo/blocked → in_progress)", cmd_task_claim),
        ("block", "Mark a task blocked with a reason", cmd_task_block),
        ("verify", "Run a task's acceptance steps", cmd_task_verify),
        ("done", "Verify acceptance and mark done", cmd_task_done),
    ]:
        parser_i = task_sub.add_parser(name, help=help_text)
        if name == "verify":
            parser_i.add_argument("--id", help="Task id (omit when using --all)")
            parser_i.add_argument(
                "--all", action="store_true", help="Verify every task on the board"
            )
        elif name != "list":
            parser_i.add_argument("--id", required=True, help="Task id")
        parser_i.add_argument("--tasks-dir", default="tasks")
        if name in ("list", "verify"):
            parser_i.add_argument("--status", choices=list(task_mod.TASK_STATUSES))
        if name == "claim":
            parser_i.add_argument("--by", required=True, help="Worker/agent name")
            parser_i.add_argument(
                "--force",
                action="store_true",
                help="Claim even if dependencies are not done (recorded in the log)",
            )
        if name == "block":
            parser_i.add_argument("--reason", required=True, help="Why the task is blocked")
        if name == "done":
            parser_i.add_argument("--by", default=None, help="Worker/agent name")
        if name in ("verify", "done"):
            parser_i.add_argument("--root", default=".", help="Repo root (default: cwd)")
            parser_i.add_argument("--results-dir", default="results")
        parser_i.set_defaults(func=func)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
