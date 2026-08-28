"""Command-line interface for the harness.

Usage::

    python -m harness verify --spec configs/demo.yaml [--results-dir DIR]
    python -m harness hash <file> [<file> ...]

    # Two-tier orchestration (Planner → tasks → Workers)
    python -m harness plan validate <plan.yaml>
    python -m harness plan materialize <plan.yaml> [--tasks-dir tasks] [--force]
    python -m harness plan status <plan.yaml> [--tasks-dir tasks]
    python -m harness task list [--status todo] [--tasks-dir tasks]
    python -m harness task show --id <id> [--tasks-dir tasks]
    python -m harness task claim --id <id> --by <agent> [--tasks-dir tasks]
    python -m harness task block --id <id> --reason "..." [--tasks-dir tasks]
    python -m harness task verify --id <id> [--tasks-dir tasks]
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
        task = task_mod.claim(args.tasks_dir, args.id, args.by)
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
    try:
        task = task_mod.load_task(args.tasks_dir, args.id)
        result = task_mod.verify_task(task, root=args.root, results_dir=args.results_dir)
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    status = "PASSED" if result.success else "FAILED"
    print(f"[{result.spec_name}] {status} ({len(result.steps)} step(s))")
    _print_step_failures(result)
    print(f"  report: {Path(result.run_dir) / 'report.md'}")
    return 0 if result.success else 1


def cmd_task_done(args: argparse.Namespace) -> int:
    try:
        task, result = task_mod.complete(args.tasks_dir, args.id, worker=args.by)
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
        if name != "list":
            parser_i.add_argument("--id", required=True, help="Task id")
        parser_i.add_argument("--tasks-dir", default="tasks")
        if name == "list":
            parser_i.add_argument("--status", choices=list(task_mod.TASK_STATUSES))
        if name == "claim":
            parser_i.add_argument("--by", required=True, help="Worker/agent name")
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
