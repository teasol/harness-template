"""Command-line interface for the harness.

Usage::

    python -m harness status                          # where am I, what next
    python -m harness setup [--platform P --model M --effort E]  # choose the Worker tier
    python -m harness verify --spec configs/demo.yaml [--results-dir DIR]
    python -m harness reproduce --spec configs/demo.yaml [--times 2]
    python -m harness hash <file> [<file> ...]

    # Two-tier orchestration (Planner → tasks → Workers)
    python -m harness plan validate <plan.yaml>
    python -m harness plan materialize <plan.yaml> [--tasks-dir tasks] [--force]
    python -m harness plan status <plan.yaml> [--tasks-dir tasks] [--check]
    python -m harness plan check [--plans-dir plans]   # every plan, no name needed
    python -m harness task list [--status todo] [--tasks-dir tasks]
    python -m harness task show --id <id> [--tasks-dir tasks]
    python -m harness task claim --id <id> --by <agent> [--force] [--tasks-dir tasks]
    python -m harness task block --id <id> --reason "..." [--tasks-dir tasks]
    python -m harness task verify (--id <id> | --all) [--status S] [--tasks-dir tasks]
    python -m harness task run --id <id> [--attempts N]   # invoke a Worker + verify
    python -m harness task done --id <id> [--by <agent>] [--tasks-dir tasks]

    # Experiments (researcher <-> Planner): one hypothesis per branch+worktree
    python -m harness exp start <name> [--question "..."]   # creates + briefs the Planner
    python -m harness exp question <name> [--set "..."]   # record it later
    python -m harness exp list
    python -m harness exp report <name> [--no-run] [--determinism] [--save]
    python -m harness exp remove <name> [--force]
    python -m harness planner brief <name>            # current state, re-run anytime
    python -m harness planner run <name>               # spawn a Planner (Tier 1 -> 2)

"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from harness import adoption as adoption_mod
from harness import experiment as exp_mod
from harness import heartbeat
from harness import plan as plan_mod
from harness import planners as planners_mod
from harness import project as project_mod
from harness import task as task_mod
from harness.experiment import ExperimentError
from harness.paths import (
    get_configs_dir,
    get_plans_dir,
    get_tasks_dir,
)
from harness.plan import PlanError, load_plan
from harness.report import write_reports
from harness.reproduce import (
    ReproduceError,
    reproduce,
    write_reproduce_report,
)
from harness.reproducibility import file_sha256
from harness.runner import Runner
from harness.setup import (
    SetupError,
    build_config,
    load_platforms,
    write_agent_config,
)
from harness.spec import SpecError, load_spec
from harness.task import TaskError
from harness.worker import (
    WorkerError,
    load_agent_config,
    load_worker_config,
    reconcile_worker_record,
    run_task,
    write_worker_report,
)


def _resolve_tasks_dir(tasks_dir: str, root: str | Path = ".") -> str:
    if tasks_dir == "tasks" and not (Path(root) / "tasks").is_dir():
        return str(get_tasks_dir(root))
    return tasks_dir


def _resolve_plans_dir(plans_dir: str, root: str | Path = ".") -> str:
    if plans_dir == "plans" and not (Path(root) / "plans").is_dir():
        return str(get_plans_dir(root))
    return plans_dir


def _resolve_spec_path(spec: str, root: str | Path = ".") -> str:
    """Let `configs/x.yaml` find `.harness/configs/x.yaml`.

    `init` writes specs under `.harness/` so they cannot collide with a
    project's own `configs/`, but the README, the Makefile and every habit say
    `configs/demo.yaml`. Tasks and plans already resolve both ways; specs did
    not, so the first command in the quickstart failed on a fresh project.
    """
    candidate = Path(spec)
    if candidate.is_absolute() or candidate.exists():
        return spec
    parts = candidate.parts
    if len(parts) >= 2 and parts[0] == "configs":
        fallback = get_configs_dir(root).joinpath(*parts[1:])
        if fallback.is_file():
            return str(fallback)
    return spec


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(_resolve_spec_path(args.spec, args.root))
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
        spec = load_spec(_resolve_spec_path(args.spec, args.root))
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
    tasks_dir = _resolve_tasks_dir(args.tasks_dir)
    try:
        plan = load_plan(args.plan)
        written = task_mod.materialize(plan, tasks_dir, force=args.force)
    except (PlanError, TaskError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if written:
        for path in written:
            print(f"  wrote {path}")
    else:
        print("  all task files already exist (use --force to overwrite)")
    return 0


def cmd_plan_check(args: argparse.Namespace) -> int:
    """Validate every plan and flag drift — with no plan name hardcoded.

    Lets the Makefile, pre-commit, and CI gate a project's plans without
    naming one, so removing the shipped demo (or adding a plan) needs no
    edits to any of them.
    """
    plans_dir = _resolve_plans_dir(args.plans_dir)
    tasks_dir = _resolve_tasks_dir(args.tasks_dir)
    plans = sorted(Path(plans_dir).glob("*.yaml"))
    if not plans:
        print(f"(no plans in {plans_dir})")
        return 0
    problems = 0
    for path in plans:
        try:
            plan = load_plan(path)
        except PlanError as exc:
            print(f"  INVALID {path}: {exc}", file=sys.stderr)
            problems += 1
            continue
        drift = task_mod.spec_drift(plan, tasks_dir)
        # A plan whose tasks were never materialized is a normal early state,
        # not drift; only a materialized task that disagrees is a problem.
        stale = {k: v for k, v in drift.items() if v != ["(not materialized)"]}
        if stale:
            for module_id, fields in stale.items():
                print(f"  DRIFT   {path}: {module_id}: {', '.join(fields)}", file=sys.stderr)
            problems += 1
        else:
            unmaterialized = len(drift)
            note = f" ({unmaterialized} module(s) not yet materialized)" if unmaterialized else ""
            print(f"  ok      {path} — {len(plan.modules)} module(s){note}")
    if problems:
        print(f"{problems} plan(s) with problems", file=sys.stderr)
    return 1 if problems else 0


def cmd_plan_status(args: argparse.Namespace) -> int:
    tasks_dir = _resolve_tasks_dir(args.tasks_dir)
    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    board = {t.id: t for t in task_mod.load_board(tasks_dir)}
    module_ids = [m.id for m in plan.modules]
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
    # Count only this plan's modules: tasks/ can hold files from other plans,
    # and counting those would report progress the plan has not made.
    done = sum(1 for i in module_ids if i in board and board[i].is_done)
    total = len(module_ids)
    print()
    print(f"Progress: {done}/{total} done")
    if plan.integration:
        print(f"Integration spec: {plan.integration}")

    orphans = sorted(set(board) - set(module_ids))
    if orphans:
        print()
        print(f"Task file(s) not in this plan (ignored here): {', '.join(orphans)}")

    drift = task_mod.spec_drift(plan, tasks_dir)
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
    tasks_dir = _resolve_tasks_dir(args.tasks_dir)
    board = task_mod.load_board(tasks_dir)
    if args.status:
        board = [t for t in board if t.status == args.status]
    if not board:
        print("(no tasks)")
        return 0
    if args.plan:
        board = [t for t in board if t.plan == args.plan]
        if not board:
            print(f"(no tasks for plan '{args.plan}')")
            return 0
    ready = set(task_mod.ready_task_ids(task_mod.load_board(tasks_dir)))
    print("  ID           PLAN            STATUS        WORKER           DEPENDS ON     READY")
    for task in board:
        deps = ", ".join(task.depends_on) or "-"
        can_start = "yes" if task.id in ready else "-"
        print(
            f"  {task.id:<12} {(task.plan or '-'):<15} {task.status:<13} "
            f"{str(task.worker or '-'):<16} {deps:<14} {can_start}"
        )
    return 0


def cmd_task_show(args: argparse.Namespace) -> int:
    tasks_dir = _resolve_tasks_dir(args.tasks_dir)
    try:
        task = task_mod.load_task(tasks_dir, args.id)
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(Path(task.path).read_text(encoding="utf-8"))
    return 0


def cmd_task_claim(args: argparse.Namespace) -> int:
    tasks_dir = _resolve_tasks_dir(args.tasks_dir)
    try:
        task = task_mod.claim(tasks_dir, args.id, args.by, force=args.force)
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"task '{task.id}' claimed by {args.by} → in_progress")
    return 0


def cmd_task_block(args: argparse.Namespace) -> int:
    tasks_dir = _resolve_tasks_dir(args.tasks_dir)
    try:
        task = task_mod.block(tasks_dir, args.id, args.reason)
    except TaskError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"task '{task.id}' → blocked ({args.reason})")
    return 0


def cmd_task_verify(args: argparse.Namespace) -> int:
    tasks_dir = _resolve_tasks_dir(args.tasks_dir, args.root)
    if not args.all and not args.id:
        print("error: task verify requires --id <id> or --all", file=sys.stderr)
        return 2
    try:
        if args.all:
            tasks = task_mod.load_board(tasks_dir)
            if args.status:
                tasks = [t for t in tasks if t.status == args.status]
        else:
            tasks = [task_mod.load_task(tasks_dir, args.id)]
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
    tasks_dir = _resolve_tasks_dir(args.tasks_dir, args.root)
    try:
        task, result = task_mod.complete(
            tasks_dir,
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
    # A task finished by hand after a Worker gave up would otherwise leave
    # worker.json permanently claiming 'failed' for a task the board calls
    # 'done'. Two records disagreeing about one event is worse than one record.
    record = reconcile_worker_record(
        task.id,
        task.status,
        results_dir=args.results_dir,
        root=args.root,
        note=f"marked done by {args.by or 'a human'} after verification",
    )
    print(f"task '{task.id}' → done")
    if record is not None:
        print(f"  worker record reconciled: {record}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Agent-first verification harness for reproducible research.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_cmd = sub.add_parser(
        "init", help="Initialize a new Research Harness project in a directory"
    )
    init_cmd.add_argument(
        "target_dir",
        nargs="?",
        default=".",
        help="Directory to initialize (default: current directory)",
    )
    init_cmd.add_argument("--name", default=None, help="Project name (slugified if needed)")
    init_cmd.add_argument(
        "--force", action="store_true", help="Overwrite existing files if already initialized"
    )
    init_cmd.add_argument(
        "--no-setup", action="store_true", help="Skip interactive agent tier setup"
    )
    init_cmd.set_defaults(func=cmd_init)

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

    plan_check = plan_sub.add_parser(
        "check", help="Validate every plan and flag drift (no plan name needed)"
    )
    plan_check.add_argument("--plans-dir", default="plans")
    plan_check.add_argument("--tasks-dir", default="tasks")
    plan_check.set_defaults(func=cmd_plan_check)

    plan_run = plan_sub.add_parser("run", help="Run every ready task through a Worker")
    plan_run.add_argument("plan", help="Path to the plan YAML file")
    plan_run.add_argument("--tasks-dir", default="tasks")
    plan_run.add_argument("--root", default=".", help="Repo root (default: cwd)")
    plan_run.add_argument("--results-dir", default="results")
    plan_run.add_argument("--worker-config", default=None, help="Worker config YAML")
    plan_run.add_argument("--attempts", type=int, default=None, help="Override the attempt cap")
    plan_run.add_argument(
        "--skip-approval",
        action="store_true",
        help="Run without a recorded approval (for automation that approved elsewhere)",
    )
    plan_run.set_defaults(func=cmd_plan_run)

    plan_approve = plan_sub.add_parser(
        "approve", help="Record agreement to run this plan, and show what it will cost"
    )
    plan_approve.add_argument("plan", help="Path to the plan YAML file")
    plan_approve.add_argument("--by", required=True, help="Who is approving")
    plan_approve.add_argument("--note", default="", help="Why, or what was changed first")
    plan_approve.add_argument("--root", default=".", help="Repo root (default: cwd)")
    plan_approve.add_argument("--worker-config", default=None, help="Worker config YAML")
    plan_approve.set_defaults(func=cmd_plan_approve)

    task_cmd = sub.add_parser("task", help="Worker task commands")
    task_sub = task_cmd.add_subparsers(dest="task_command", required=True)

    for name, help_text, func in [
        ("list", "List tasks on the board", cmd_task_list),
        ("show", "Print a task file", cmd_task_show),
        ("claim", "Claim a task (todo/blocked → in_progress)", cmd_task_claim),
        ("block", "Mark a task blocked with a reason", cmd_task_block),
        ("verify", "Run a task's acceptance steps", cmd_task_verify),
        ("done", "Verify acceptance and mark done", cmd_task_done),
        ("run", "Invoke a Worker on a task, verify, and retry", cmd_task_run),
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
        if name == "list":
            parser_i.add_argument("--plan", default=None, help="Only tasks from this plan")
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
        if name in ("verify", "done", "run"):
            parser_i.add_argument("--root", default=".", help="Repo root (default: cwd)")
            parser_i.add_argument("--results-dir", default="results")
        if name == "run":
            parser_i.add_argument("--by", default=None, help="Worker/agent label")
            parser_i.add_argument("--worker-config", default=None, help="Worker config YAML")
            parser_i.add_argument(
                "--attempts", type=int, default=None, help="Override the attempt cap"
            )
            parser_i.add_argument(
                "--adapter", choices=["manual", "cli"], default=None, help="Override the adapter"
            )
        parser_i.set_defaults(func=func)

    exp_cmd = sub.add_parser("exp", help="Experiment commands (researcher <-> Planner)")
    exp_sub = exp_cmd.add_subparsers(dest="exp_command", required=True)

    exp_start = exp_sub.add_parser("start", help="Create an experiment branch + worktree")
    exp_start.add_argument("name", help="Experiment name (lowercase, hyphens)")
    exp_start.add_argument("--base", default="HEAD", help="Commit/branch to branch from")
    exp_start.add_argument(
        "--question",
        default=None,
        help="The research question, verbatim. Optional — settle it with the Planner instead",
    )
    exp_start.add_argument(
        "--planner", default="planner", help="Label recorded for this experiment's Planner"
    )
    exp_start.add_argument("--model", default=None, help="Model this Planner runs on (recorded)")
    exp_start.add_argument("--effort", default=None, help="Reasoning level of the Planner")
    exp_start.add_argument(
        "--path",
        default=exp_mod.DEFAULT_WORKTREE_ROOT,
        help="Directory holding experiment worktrees",
    )
    exp_start.set_defaults(func=cmd_exp_start)

    exp_list = exp_sub.add_parser("list", help="List experiments")
    exp_list.set_defaults(func=cmd_exp_list)

    exp_report = exp_sub.add_parser("report", help="Build the researcher's merge decision aid")
    exp_report.add_argument("name", help="Experiment name")
    exp_report.add_argument("--no-run", action="store_true", help="Do not run the integration spec")
    exp_report.add_argument(
        "--determinism", action="store_true", help="Also run the determinism gate"
    )
    exp_report.add_argument(
        "--save", action="store_true", help="Also write the report into the experiment branch"
    )
    exp_report.set_defaults(func=cmd_exp_report)

    exp_question = exp_sub.add_parser("question", help="Show or record the experiment's question")
    exp_question.add_argument("name", help="Experiment name")
    exp_question.add_argument("--set", default=None, help="Record this as the question (verbatim)")
    exp_question.set_defaults(func=cmd_exp_question)

    exp_remove = exp_sub.add_parser("remove", help="Remove an experiment worktree (keeps branch)")
    exp_remove.add_argument("name", help="Experiment name")
    exp_remove.add_argument("--force", action="store_true", help="Remove even if dirty")
    exp_remove.set_defaults(func=cmd_exp_remove)

    for exp_parser in (exp_start, exp_list, exp_report, exp_remove, exp_question):
        exp_parser.add_argument("--root", default=".", help="Repo root (default: cwd)")

    status_cmd = sub.add_parser("status", help="Where am I and what do I do next? (start here)")
    status_cmd.add_argument("--root", default=".", help="Repo root (default: cwd)")
    status_cmd.set_defaults(func=cmd_status)

    setup_cmd = sub.add_parser(
        "setup", help="Choose the Worker platform, model, and reasoning level"
    )
    for tier, default_attempts in (("planner", 3), ("worker", 6)):
        setup_cmd.add_argument(f"--{tier}-platform", default=None, help=f"{tier} platform")
        setup_cmd.add_argument(f"--{tier}-model", default=None, help=f"{tier} model")
        setup_cmd.add_argument(f"--{tier}-effort", default=None, help=f"{tier} reasoning level")
        setup_cmd.add_argument(
            f"--{tier}-command", default=None, help=f"override the {tier} command"
        )
        setup_cmd.add_argument(
            f"--{tier}-session", default=None, help=f"attach {tier} to an existing session id"
        )
        setup_cmd.add_argument(
            f"--{tier}-attempts", type=int, default=default_attempts, help=f"{tier} retry cap"
        )
    setup_cmd.add_argument(
        "--check",
        action="store_true",
        help="Smoke-test the configured agent commands and exit (spawns one cheap prompt)",
    )
    setup_cmd.add_argument("--label", default="worker", help="Name recorded on the board")
    setup_cmd.add_argument("--list", action="store_true", help="List platforms and exit")
    setup_cmd.add_argument("--platforms", default=None, help="Platform presets YAML")
    setup_cmd.add_argument("--out", default=None, help="Where to write the agent config")
    setup_cmd.add_argument("--root", default=".", help="Repo root (default: cwd)")
    setup_cmd.set_defaults(func=cmd_setup)

    progress_cmd = sub.add_parser(
        "progress", help="Where the harness is right now (run it in another terminal)"
    )
    progress_cmd.add_argument("--root", default=".", help="Repo root (default: cwd)")
    progress_cmd.add_argument("--results-dir", default="results")
    progress_cmd.add_argument(
        "--watch",
        nargs="?",
        type=float,
        const=5.0,
        default=None,
        metavar="SECONDS",
        help="Refresh until interrupted (default every 5s)",
    )
    progress_cmd.set_defaults(func=cmd_progress)

    create_cmd = sub.add_parser(
        "create",
        help="Create a Planner (the only thing the harness creates)",
    )
    create_cmd.add_argument("-n", "--name", required=True, help="Planner name")
    create_cmd.add_argument("--model", required=True, help="Model this Planner runs on")
    create_cmd.add_argument("--effort", default=None, help="Reasoning level")
    create_cmd.add_argument("--root", default=".", help="Repo root (default: cwd)")
    create_cmd.set_defaults(func=cmd_create)

    project_cmd = sub.add_parser(
        "project", help="What a Planner must know before it plans anything here"
    )
    project_sub = project_cmd.add_subparsers(dest="project_command", required=True)
    project_init = project_sub.add_parser("init", help="Scaffold configs/project.yaml")
    project_init.add_argument("--root", default=".", help="Repo root (default: cwd)")
    project_init.add_argument("--force", action="store_true", help="Overwrite an existing file")
    project_init.set_defaults(func=cmd_project_init)
    project_show = project_sub.add_parser(
        "show", help="Print the project context and flag paths that do not exist"
    )
    project_show.add_argument("--root", default=".", help="Repo root (default: cwd)")
    project_show.set_defaults(func=cmd_project_show)

    planner_cmd = sub.add_parser("planner", help="Planner registration")
    planner_sub = planner_cmd.add_subparsers(dest="planner_command", required=True)
    planner_create = planner_sub.add_parser(
        "create", help="Register a Planner that outlives one experiment"
    )
    planner_create.add_argument("name", help="Planner name (lowercase, digits, hyphens)")
    planner_create.add_argument("--model", required=True, help="Model this Planner runs on")
    planner_create.add_argument("--effort", default=None, help="Reasoning level")
    planner_create.add_argument("--root", default=".", help="Repo root (default: cwd)")
    planner_create.set_defaults(func=cmd_planner_create)

    planner_list = planner_sub.add_parser("list", help="Every registered Planner")
    planner_list.add_argument("--root", default=".", help="Repo root (default: cwd)")
    planner_list.set_defaults(func=cmd_planner_list)

    planner_show = planner_sub.add_parser("show", help="One Planner and everything it knows")
    planner_show.add_argument("name", help="Planner name")
    planner_show.add_argument("--root", default=".", help="Repo root (default: cwd)")
    planner_show.set_defaults(func=cmd_planner_show)

    planner_note = planner_sub.add_parser(
        "note", help="Record something the next run should not rediscover"
    )
    planner_note.add_argument("name", help="Planner name")
    planner_note.add_argument("--add", required=True, help="The finding, in one sentence")
    planner_note.add_argument("--experiment", default=None, help="Where it was learned")
    planner_note.add_argument("--root", default=".", help="Repo root (default: cwd)")
    planner_note.set_defaults(func=cmd_planner_note)

    planner_brief = planner_sub.add_parser(
        "brief", help="Print everything a session needs to act as this experiment's Planner"
    )
    planner_brief.add_argument("name", help="Experiment name")
    planner_brief.add_argument(
        "--register", default=None, help="Record this label as the experiment's Planner"
    )
    planner_brief.add_argument(
        "--model", default=None, help="Model this Planner session runs on (recorded, not set)"
    )
    planner_brief.add_argument(
        "--effort", default=None, help="Reasoning level of this Planner session (recorded)"
    )
    planner_brief.add_argument(
        "--session", default=None, help="Session id this Planner is running in (recorded)"
    )
    planner_brief.add_argument("--root", default=".", help="Repo root (default: cwd)")
    planner_brief.set_defaults(func=cmd_planner_brief)

    planner_run = planner_sub.add_parser(
        "run", help="Spawn a Planner and drive the experiment to a reportable state"
    )
    planner_run.add_argument("name", help="Experiment name")
    planner_run.add_argument("--attempts", type=int, default=None, help="Override the attempt cap")
    planner_run.add_argument("--config", default=None, help="Agent config YAML")
    planner_run.add_argument("--root", default=".", help="Repo root (default: cwd)")
    planner_run.set_defaults(func=cmd_planner_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


# ---------------------------------------------------------------------------
# Experiments (researcher <-> Planner)


def cmd_exp_start(args: argparse.Namespace) -> int:
    """Create an experiment and brief its Planner — one experiment, one Planner."""
    try:
        experiment = exp_mod.start(
            args.name,
            root=args.root,
            worktree_root=args.path,
            base=args.base,
            question=args.question or "",
        )
        # A registered Planner already knows what it runs on and what it has
        # learned, so starting an experiment under one carries both across
        # instead of beginning from nothing.
        model, effort = (args.model or ""), (args.effort or "")
        if planners_mod.exists(args.planner, args.root):
            planner = planners_mod.link_experiment(args.planner, args.name, root=args.root)
            model = model or planner.model
            effort = effort or planner.effort

        # At creation time nobody knows which model will drive this yet, so an
        # unknown model leaves the Planner unregistered rather than recorded as
        # blank. The briefing then opens by asking the session to say what it
        # is — a placeholder would just look like an answer.
        if model.strip():
            exp_mod.register_planner(
                args.name,
                args.planner,
                root=args.root,
                model=model,
                effort=effort,
            )
        brief = exp_mod.planner_brief(args.name, root=args.root)
    except ExperimentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Experiment '{experiment.name}' created on {experiment.branch}.")
    print(f"Its Planner is '{args.planner}'. Everything below is that Planner's briefing —")
    print("follow it, or hand this session to whoever will.\n")
    if not planners_mod.exists(args.planner, args.root):
        # Said once, here, because this is the last moment it is cheap: the
        # briefing below is about to be written without any model on record and
        # without whatever an earlier Planner learned in this project.
        print(
            f"note: '{args.planner}' is a label, not a registered Planner. This experiment\n"
            "      therefore carries no Planner model, and its report will say so. Register\n"
            "      one and future experiments inherit its model and its notes:\n"
            f"        harness create -n {args.planner} --model <model>\n"
        )
    print("=" * 70)
    print(brief)
    return 0


def cmd_exp_list(args: argparse.Namespace) -> int:
    experiments = exp_mod.list_experiments(args.root)
    if not experiments:
        print("(no experiments)")
        return 0
    print("  NAME              BRANCH                 WORKTREE")
    for experiment in experiments:
        print(f"  {experiment.name:<17} {experiment.branch:<22} {experiment.path}")
    return 0


def cmd_exp_report(args: argparse.Namespace) -> int:
    try:
        report = exp_mod.build_report(
            args.name,
            root=args.root,
            run_integration=not args.no_run,
            check_determinism=args.determinism,
        )
        experiment = exp_mod.find_experiment(args.name, args.root)
    except ExperimentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    written = exp_mod.write_experiment_report(report, experiment.path, save=args.save)
    verdict = "READY TO MERGE" if report.merge_ready else "NOT READY"
    print(f"[{report.experiment}] {verdict}")
    print(f"  integration: {report.integration}")
    print(f"  tasks:       {report.tasks_done}/{report.tasks_total} done")
    print(f"  determinism: {report.determinism}")
    print(f"  commit:      {report.commit or 'unknown'}{' (dirty)' if report.dirty else ''}")
    for value in report.metrics:
        print(f"  {value.name}: {value.error or value.value}")
    for caveat in report.caveats:
        print(f"  not verified: {caveat}")
    for path in written:
        print(f"  report: {path}")
    if report.merge_ready:
        print(f"\n  Merging is your call: git merge {report.branch}")
    return 0 if report.merge_ready else 1


def cmd_exp_remove(args: argparse.Namespace) -> int:
    try:
        experiment = exp_mod.remove(args.name, root=args.root, force=args.force)
    except ExperimentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"removed worktree {experiment.path} (branch {experiment.branch} kept)")
    return 0


# ---------------------------------------------------------------------------
# Worker invocation (Planner -> Worker)


def _print_worker_outcome(outcome) -> None:
    for attempt in outcome.attempts:
        mark = "ok" if attempt.passed else "FAIL"
        print(
            f"  {mark:>4}  attempt {attempt.number} ({attempt.duration_s:.1f}s): {attempt.detail}"
        )
    print(f"  adapter: {outcome.adapter}")
    print(f"  cost:    {outcome.cost}")
    if outcome.brief_path:
        print(f"  brief:   {outcome.brief_path}")
    if outcome.message:
        print(f"  {outcome.message}")


def cmd_task_run(args: argparse.Namespace) -> int:
    tasks_dir = _resolve_tasks_dir(args.tasks_dir, args.root)
    try:
        config = load_worker_config(args.worker_config, root=args.root)
        if args.attempts is not None:
            config.attempts = args.attempts
        if args.adapter:
            config.adapter = args.adapter
        outcome = run_task(
            tasks_dir,
            args.id,
            config=config,
            root=args.root,
            results_dir=args.results_dir,
            worker_name=args.by,
            progress=_flushing_printer,
        )
    except (TaskError, WorkerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write_worker_report(outcome, args.results_dir, root=args.root)
    print(f"[{outcome.task_id}] {outcome.status.upper()}")
    _print_worker_outcome(outcome)
    return 0 if outcome.succeeded else 1


def cmd_plan_approve(args: argparse.Namespace) -> int:
    """Approve a plan — after printing what approving it commits you to."""
    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        config = load_worker_config(args.worker_config, root=args.root)
    except WorkerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cost = plan_mod.estimate_cost(plan, config.attempts, config.timeout)
    print(f"Plan: {plan.name}")
    print(f"Goal: {plan.goal.strip()[:400]}")
    print()
    for module_id in plan.topological_order():
        module = plan.module(module_id)
        deps = ", ".join(module.depends_on) or "-"
        print(f"  [{module.executor:<7}] {module_id:<24} depends on: {deps}")
    print()
    print(
        f"  Worker modules: {cost['worker_modules']} x {cost['attempts_per_module']} attempts "
        f"x {cost['timeout_s']:g}s cap = up to {cost['worst_case_s'] / 3600:.1f}h of agent time"
    )
    if cost["planner_modules"]:
        print(f"  Planner modules: {cost['planner_modules']} (you run these yourself)")
    print()

    record = plan_mod.record_approval(args.plan, args.by, args.note)
    print(f"approved by {args.by} — recorded in {record}")
    print("The approval is tied to the plan's contents: edit the plan and it lapses.")
    return 0


def _flushing_printer(line: str) -> None:
    """Print progress as it happens, not when the buffer decides to.

    `plan run` can sit for half an hour inside one attempt; a line that arrives
    only at the end is not progress reporting.
    """
    print(line)
    sys.stdout.flush()


def cmd_plan_run(args: argparse.Namespace) -> int:
    """Drain the ready queue: run each ready task in dependency order."""
    tasks_dir = _resolve_tasks_dir(args.tasks_dir, args.root)
    try:
        plan = load_plan(args.plan)
        config = load_worker_config(args.worker_config, root=args.root)
    except (PlanError, WorkerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.attempts is not None:
        config.attempts = args.attempts

    # A plan is a proposal until a person agrees to it. Everything after this
    # line spends time and money, so this is where agreement is required —
    # validating and materializing a plan stay free and unguarded.
    if not getattr(args, "skip_approval", False):
        approved, reason = plan_mod.approval_status(args.plan)
        if not approved:
            cost = plan_mod.estimate_cost(plan, config.attempts, config.timeout)
            hours = cost["worst_case_s"] / 3600
            print(f"error: {reason}.", file=sys.stderr)
            print(
                f"\nThis plan would run {cost['worker_modules']} Worker module(s) at "
                f"{cost['attempts_per_module']} attempts and a {cost['timeout_s']:g}s cap "
                f"— up to {hours:.1f}h of agent time"
                + (
                    f", plus {cost['planner_modules']} module(s) you run yourself."
                    if cost["planner_modules"]
                    else "."
                ),
                file=sys.stderr,
            )
            print(
                f"\nRead the plan, then:\n"
                f"  python -m harness plan approve {args.plan} --by <who>\n",
                file=sys.stderr,
            )
            return 2
        print(f"plan {reason}\n")

    if config.adapter == "manual":
        print(
            "worker adapter is 'manual': each task writes a briefing and stops.\n"
            "Set `adapter: cli` in configs/worker.yaml to have Workers built here.\n"
        )

    completed = 0
    while True:
        board = task_mod.load_board(tasks_dir)
        by_id = {t.id: t for t in board}
        # Modules the Planner claimed are not the drain loop's to run; skipping
        # them lets the Worker queue finish instead of halting on the first one.
        ready = [t for t in task_mod.ready_task_ids(board) if by_id[t].executor != "planner"]
        if not ready:
            break
        task_id = ready[0]
        total_modules = len(plan.modules)
        module_index = plan.topological_order().index(task_id) + 1
        print(f"--- running task '{task_id}' (module {module_index}/{total_modules}) ---")
        sys.stdout.flush()
        try:
            outcome = run_task(
                tasks_dir,
                task_id,
                config=config,
                root=args.root,
                results_dir=args.results_dir,
                position=(module_index, total_modules),
                progress=_flushing_printer,
            )
        except (TaskError, WorkerError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        write_worker_report(outcome, args.results_dir, root=args.root)
        print(f"[{task_id}] {outcome.status.upper()}")
        _print_worker_outcome(outcome)
        if not outcome.succeeded:
            print(f"stopping: '{task_id}' did not finish", file=sys.stderr)
            return 1
        completed += 1

    board = task_mod.load_board(args.tasks_dir)
    done = sum(1 for t in board if t.is_done)
    print(f"\n{completed} task(s) run this pass; {done}/{len(plan.modules)} module(s) done")
    if done < len(plan.modules):
        # "no ready tasks left" on its own is the least useful sentence the
        # harness can print: it is identical whether a task is blocked, waiting
        # on a dependency, claimed by someone, or the Planner's own to do.
        print("no ready tasks left. Remaining modules:")
        for task in board:
            if task.is_done:
                continue
            if task.executor == "planner":
                why = "yours to run (`executor: planner`)"
            elif task.status == "blocked":
                why = "blocked — read its log, then fix the brief or the acceptance"
            elif task.status == "in_progress":
                why = f"claimed by {task.worker or 'someone'} — `task done` or re-claim it"
            else:
                waiting = [
                    d for d in task.depends_on if d not in {t.id for t in board if t.is_done}
                ]
                why = f"waiting on {', '.join(waiting)}" if waiting else f"status '{task.status}'"
            print(f"  {task.id:<24} {why}")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Progress


def cmd_progress(args: argparse.Namespace) -> int:
    """Say where the harness is right now — from a second terminal, while it works."""
    import time as _time

    results_dir = Path(args.root) / args.results_dir

    def render() -> int:
        beat = heartbeat.read(results_dir)
        if beat is None:
            print("Nothing running.")
            print(f"  (looked in {heartbeat.heartbeat_path(results_dir)})")
            return 1
        print(beat.describe())
        if beat.detail.get("log"):
            print(f"  output so far: {beat.detail['log']}")
        if beat.pid:
            print(f"  pid {beat.pid}")
        return 0

    if not args.watch:
        return render()

    try:
        while True:
            print("\033[2J\033[H", end="")
            print(f"harness progress — refreshing every {args.watch}s, Ctrl-C to stop\n")
            render()
            _time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
# Project context


def cmd_project_init(args: argparse.Namespace) -> int:
    try:
        path = project_mod.write_template(args.root, force=args.force)
    except project_mod.ProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {path}")
    print(
        "Edit it, then every Planner briefing opens with it.\n"
        "The one that matters most is `docs.authority`: the document that wins\n"
        "when two sources disagree about a number."
    )
    return 0


def cmd_project_show(args: argparse.Namespace) -> int:
    try:
        context = project_mod.load_project_context(args.root)
    except project_mod.ProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if context.is_empty:
        print("No project context declared.")
        print(f"  expected at: {project_mod.config_path(args.root)}")
        print("  create one:  python -m harness project init")
        return 1

    print(f"Project context: {context.source}\n")
    if context.authority_doc:
        print(f"  numbers of record  {context.authority_doc}")
    for name, rel in sorted(context.docs.items()):
        if name != "authority":
            print(f"  {name:<18} {rel}")
    if context.report_format:
        print(f"  {'report format':<18} {context.report_format}")
    if context.environment:
        print(f"  {'environment':<18} {context.environment}")
    if context.python:
        print(f"  {'project python':<18} {context.python}  (steps: ${{PROJECT_PYTHON}})")
    if context.conventions:
        print("\n  conventions:")
        for i, rule in enumerate(context.conventions, 1):
            print(f"    {i}. {rule}")

    gaps = project_mod.missing_docs(context, args.root)
    if gaps:
        print("\n  DECLARED BUT MISSING — a Planner would be sent to nothing:")
        for gap in gaps:
            print(f"    {gap}")
        return 1
    print()
    return 0


# ---------------------------------------------------------------------------
# Planner registration


def cmd_create(args: argparse.Namespace) -> int:
    """Create a Planner. It is the only thing this harness creates."""
    try:
        planner = planners_mod.create(args.name, args.model, args.effort or "", root=args.root)
    except planners_mod.PlannerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    tier = planner.model + (f", effort {planner.effort}" if planner.effort else "")
    print(f"Planner '{planner.name}' created ({tier}).")
    print(f"  record: {planner.path}")
    print(
        f"\nStart experiments under it, and they inherit its model and its notes:\n"
        f"  harness exp start <name> --planner {planner.name}"
    )
    return 0


def cmd_planner_create(args: argparse.Namespace) -> int:
    try:
        planner = planners_mod.create(args.name, args.model, args.effort or "", root=args.root)
    except planners_mod.PlannerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"Planner '{planner.name}' created ({planner.model}"
        + (f", effort {planner.effort}" if planner.effort else "")
        + ")."
    )
    print(f"  record: {planner.path}")
    print(
        f"\nStart experiments under it, and they share its memory:\n"
        f"  python -m harness exp start <name> --planner {planner.name}"
    )
    return 0


def cmd_planner_list(args: argparse.Namespace) -> int:
    planners = planners_mod.list_planners(args.root)
    if not planners:
        print("No planners registered.")
        print("  create one: python -m harness planner create <name> --model <model>")
        return 1
    print(f"  {'NAME':<16} {'MODEL':<28} {'EFFORT':<8} EXPERIMENTS  NOTES")
    for planner in planners:
        print(
            f"  {planner.name:<16} {planner.model:<28} {planner.effort or '-':<8} "
            f"{len(planner.experiments):<12} {len(planner.notes)}"
        )
    return 0


def cmd_planner_show(args: argparse.Namespace) -> int:
    try:
        planner = planners_mod.load(args.name, args.root)
    except planners_mod.PlannerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Planner: {planner.name}")
    print(f"  model:   {planner.model}" + (f" (effort {planner.effort})" if planner.effort else ""))
    print(f"  created: {planner.created_at}")
    print(f"  drove:   {', '.join(planner.experiments) or '(none yet)'}")
    if planner.notes:
        print(f"\n  Carried forward ({len(planner.notes)}):")
        for note in planner.notes:
            where = f" [{note.experiment}]" if note.experiment else ""
            print(f"    - {note.text}{where}")
            print(f"      {note.at}")
    else:
        print("\n  No notes yet.")
    return 0


def cmd_planner_note(args: argparse.Namespace) -> int:
    try:
        planner = planners_mod.add_note(
            args.name, args.add, experiment=args.experiment or "", root=args.root
        )
    except planners_mod.PlannerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"recorded — {planner.name} now carries {len(planner.notes)} note(s) forward")
    return 0


def cmd_planner_brief(args: argparse.Namespace) -> int:
    try:
        if args.register:
            exp_mod.register_planner(
                args.name,
                args.register,
                root=args.root,
                model=args.model or "",
                effort=args.effort or "",
                require_model=True,
            )
        print(exp_mod.planner_brief(args.name, root=args.root))
    except ExperimentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


# ---------------------------------------------------------------------------
# Orientation


def cmd_status(args: argparse.Namespace) -> int:
    status = exp_mod.project_status(args.root)
    print(f"Project: {status.project_name}\n")

    # What is happening *now* outranks what the board says, and is the one
    # thing a stale-looking board cannot tell you.
    beat = heartbeat.read(Path(args.root) / "results")
    if beat is not None:
        print(f"  RUNNING NOW: {beat.describe()}")
        print("  watch it:    harness progress --watch\n")

    print(status.headline)

    if status.experiments:
        print()
        print("  EXPERIMENT        STATE              DETAIL")
        for exp in status.experiments:
            marker = "*" if exp.name == status.here else " "
            print(f" {marker}{exp.name:<17} {exp.state:<18} {exp.detail}")

    def _tier_line(name: str, tier: dict) -> str | None:
        if tier.get("adapter") != "cli":
            return None
        shown = " · ".join(
            x for x in (tier.get("platform"), tier.get("model"), tier.get("effort")) if x
        )
        attached = f"  (session {tier['session']})" if tier.get("session") else ""
        return f"  {name}: {shown or 'cli'}{attached}"

    tier_lines = [
        line
        for line in (
            _tier_line("Planner runs on", status.planner_tier),
            _tier_line("Workers run on", status.worker_tier),
        )
        if line
    ]
    if tier_lines:
        print()
        for line in tier_lines:
            print(line)
    if status.worker_adapter == "manual" and status.experiments:
        print()
        if status.agents_config_found:
            print(
                "  Agents are MANUAL: the harness writes a briefing and stops rather\n"
                "  than spawning anything. Run `harness setup` to pick the platform,\n"
                "  model, and reasoning level for each tier."
            )
        else:
            # Chosen-manual and fallen-back-to-manual used to look identical,
            # so a worktree that never inherited its agent config looked like a
            # working setup that simply had nothing to do.
            print(
                "  WARNING: no agent configuration found here, so agents fell back\n"
                "  to MANUAL — `plan run` will write briefings and spawn nothing.\n"
                f"  Expected: {status.agents_config_path}\n"
                "  Copy it from the parent project, or run `harness setup`."
            )

    print()
    print("Next:")
    for step in status.next_steps:
        print(f"  {step}")
    print()
    print("New here? README.md walks through a whole experiment, start to finish.")
    return 0


# ---------------------------------------------------------------------------
# First-run configuration


def _prompt(question: str, default: str, choices: list[str] | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    if choices:
        suffix = f" ({'/'.join(choices)})" + suffix
    try:
        answer = input(f"{question}{suffix}: ").strip()
    except EOFError:
        # Non-interactive (a pipe, CI): take the defaults rather than hanging.
        return default
    return answer or default


def _choose_tier(
    tier: str,
    platforms: dict,
    name: str | None,
    model: str | None,
    effort: str | None,
    command: str | None,
    session: str | None,
    interactive: bool,
):
    """Resolve one tier's platform/model/effort, asking only what is missing."""
    if not name:
        if interactive:
            print(f"\n--- {tier} ---")
            for platform in platforms.values():
                print(f"  {platform.name:<12} {platform.label}")
            name = _prompt(f"{tier} platform", next(iter(platforms)))
        else:
            name = next(iter(platforms))
    platform = platforms.get(name)
    if platform is None:
        raise SetupError(f"unknown platform '{name}'. available: {', '.join(platforms)}")

    if platform.is_manual:
        return platform, "", "", None, ""

    is_planner = tier == "planner"
    default_model = (
        (platform.planner_model or platform.default_model) if is_planner else platform.default_model
    )
    default_effort = (
        (platform.planner_effort or platform.default_effort)
        if is_planner
        else platform.default_effort
    )

    if model is None:
        model = (
            _prompt(f"{tier} model ({platform.model_hint})", default_model)
            if interactive
            else default_model
        )
    if effort is None:
        effort = (
            _prompt(f"{tier} reasoning level", default_effort, platform.efforts or None)
            if interactive
            else default_effort
        )
    if session is None and interactive and platform.session_command:
        session = _prompt(f"{tier} session id to attach (blank = start fresh)", "")
    if command is None and platform.is_custom and interactive:
        command = _prompt(f"{tier} command", "")
    return platform, model, effort, command, session or ""


def _run_agent_check(root: str) -> int:
    """Probe every configured tier and report whether it can actually be driven."""
    from harness.setup import check_tier

    print("Checking configured agents (one cheap prompt each)...\n")
    failed = False
    for tier in ("planner", "worker"):
        outcome = check_tier(tier, root=root)
        mark = "ok  " if outcome.ok else "FAIL"
        timing = f" ({outcome.duration_s:.1f}s)" if outcome.duration_s else ""
        print(f"  {mark} {tier:<8} [{outcome.adapter}]{timing}  {outcome.detail}")
        if not outcome.ok:
            failed = True
            if outcome.command:
                print(f"       $ {outcome.command}")
    print()
    if failed:
        print(
            "A tier that cannot be driven fails every attempt in under a second,\n"
            "which reads as 'the agent tried and failed' rather than 'the agent was\n"
            "never reached'. Fix the command in the agent config, then re-check."
        )
        return 1
    print("All configured tiers responded.")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    if getattr(args, "check", False):
        return _run_agent_check(args.root)

    try:
        platforms = load_platforms(args.platforms, root=args.root)
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list:
        print("Agent platforms (from the presets file — edit it to add your own):\n")
        for platform in platforms.values():
            print(f"  {platform.name:<10} {platform.label}")
            if platform.efforts:
                print(f"  {'':<10} reasoning levels: {', '.join(platform.efforts)}")
            if platform.model_hint:
                print(f"  {'':<10} model: {platform.model_hint}")
            if platform.session_command:
                print(f"  {'':<10} can attach an existing session id")
            if platform.docs:
                print(f"  {'':<10} check flags with: {platform.docs}")
            print()
        return 0

    interactive = not any(
        [args.planner_platform, args.worker_platform, args.planner_model, args.worker_model]
    )
    if interactive:
        print("Two tiers to configure.\n")
        print("  planner  decomposes the goal and judges the whole — the reasoning is here")
        print("  worker   builds one fully-specified module against machine-checked acceptance")
        print("\nA Worker's task is bounded, so a small fast model usually suffices; the")
        print("expensive one belongs in planning. That difference is the point of tiering.")

    try:
        p_platform, p_model, p_effort, p_command, p_session = _choose_tier(
            "planner",
            platforms,
            args.planner_platform,
            args.planner_model,
            args.planner_effort,
            args.planner_command,
            args.planner_session,
            interactive,
        )
        w_platform, w_model, w_effort, w_command, w_session = _choose_tier(
            "worker",
            platforms,
            args.worker_platform,
            args.worker_model,
            args.worker_effort,
            args.worker_command,
            args.worker_session,
            interactive,
        )
        planner = build_config(
            p_platform,
            model=p_model,
            effort=p_effort,
            attempts=args.planner_attempts,
            command=p_command,
            label="planner",
            session=p_session,
        )
        worker = build_config(
            w_platform,
            model=w_model,
            effort=w_effort,
            attempts=args.worker_attempts,
            command=w_command,
            label=args.label,
            session=w_session,
        )
        path = write_agent_config(planner, worker, args.out, root=args.root)
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"\nwrote {path}\n")
    for tier, config in (("planner", planner), ("worker", worker)):
        if config.adapter == "manual":
            print(f"  {tier:<8} manual (copy-paste briefings into your agent session)")
            continue
        attached = f"  (attached to session {config.session})" if config.session else ""
        print(
            f"  {tier:<8} {config.platform} · {config.model or 'platform default'} · "
            f"{config.effort or 'platform default'}{attached}"
        )
    docs = {p_platform.docs, w_platform.docs} - {""}
    if docs:
        print("\nFlags change between releases — verify with: " + "; ".join(sorted(docs)))
    print("\n  harness planner run <experiment>    spawns the Planner")
    print("  harness plan run <plan>            Workers build each module")
    return 0


def cmd_planner_run(args: argparse.Namespace) -> int:
    try:
        config = load_agent_config("planner", args.config, root=args.root)
        if args.attempts is not None:
            config.attempts = args.attempts
        outcome = exp_mod.run_planner(args.name, config=config, root=args.root)
    except (ExperimentError, WorkerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"[{outcome.experiment}] {outcome.status.upper()}")
    for attempt in outcome.attempts:
        print(f"  attempt {attempt.number} ({attempt.duration_s:.1f}s): {attempt.state}")
    tier = " · ".join(x for x in (outcome.platform, outcome.model, outcome.effort) if x)
    print(f"  planner: {tier or outcome.adapter}")
    if outcome.brief_path:
        print(f"  brief:   {outcome.brief_path}")
    if outcome.message:
        print(f"  {outcome.message}")
    if outcome.succeeded:
        print(f"\n  harness exp report {outcome.experiment} --determinism --save")
    return 0 if outcome.succeeded else 1


def cmd_exp_question(args: argparse.Namespace) -> int:
    try:
        if args.set:
            experiment = exp_mod.set_question(args.name, args.set, root=args.root)
            print(f"recorded for '{experiment.name}':\n")
            print(experiment.question)
            print(f"\n  {experiment.question_path}")
            print("  Commit it — the question belongs with the experiment.")
            return 0
        experiment = exp_mod.find_experiment(args.name, args.root)
    except ExperimentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not experiment.question:
        print(
            f"experiment '{experiment.name}' has no question recorded yet.\n"
            f'  harness exp question {experiment.name} --set "..."'
        )
        return 1
    print(experiment.question)
    return 0


def find_project_root(start: str | Path = ".") -> Path:
    """Locate the project root by searching upwards for .harness, configs, or .git."""
    current = Path(start).resolve()
    for parent in [current, *current.parents]:
        if (
            (parent / ".harness").is_dir()
            or (parent / "configs" / "agents.yaml").is_file()
            or (parent / "plans").is_dir()
        ):
            return parent
        if (parent / ".git").exists():
            return parent
    return current


def cmd_init(args: argparse.Namespace) -> int:
    from harness.init import InitError, init_project

    target_dir = Path(args.target_dir)
    try:
        created = init_project(
            target_dir=target_dir,
            name=args.name,
            force=args.force,
        )
    except InitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    resolved_target = target_dir.resolve()

    def _rel(path: Path) -> Path:
        try:
            return path.relative_to(resolved_target)
        except ValueError:
            return path

    if created.already_initialized and not args.force:
        if created.created:
            print(
                f"Updated Research Harness in {resolved_target} "
                f"({len(created.created)} file(s) added):"
            )
            for path in created.created:
                print(f"  + {_rel(path)}")
        else:
            print(f"Research Harness in {resolved_target} is already up to date.")
        if created.kept:
            print(f"\n  {len(created.kept)} existing file(s) left untouched, including your")
            print("  agent configuration. Use --force to reset them to the shipped defaults.")
    else:
        print(
            f"Initialized Research Harness in {resolved_target} "
            f"({len(created.created)} file(s) created/updated):"
        )
        for path in created.created:
            print(f"  + {_rel(path)}")

    # If interactive and not --no-setup, configure agents
    if not args.no_setup and sys.stdin.isatty():
        print("\nConfiguring agent tiers for your new project:")
        setup_args = argparse.Namespace(
            planner_platform=None,
            planner_model=None,
            planner_effort=None,
            planner_command=None,
            planner_session=None,
            planner_attempts=3,
            worker_platform=None,
            worker_model=None,
            worker_effort=None,
            worker_command=None,
            worker_session=None,
            worker_attempts=6,
            label="worker",
            list=False,
            platforms=None,
            out=None,
            root=str(target_dir),
        )
        cmd_setup(setup_args)

    # Landing on an existing codebase is a different situation from starting
    # empty, and it used to print the identical next steps — so the one fact
    # that matters on day one, that none of this code is verified yet, went
    # unsaid.
    adoption = adoption_mod.record(target_dir)
    print("\nNext steps:")
    if adoption is None:
        print("  1. Create a Planner:     harness create -n <name> --model <model>")
        print("  2. Start an experiment:  harness exp start <name> --planner <name>")
        print(
            "\n  (smoke-test the harness itself any time: harness verify --spec configs/demo.yaml)"
        )
        return 0

    print(
        f"  This repository already has {adoption.source_files} source file(s), and none of\n"
        "  them is covered by a contract, an acceptance check, or a plan yet.\n"
        "  Register a Planner and let it plan how to change that:\n"
    )
    for index, step in enumerate(adoption_mod.next_steps(target_dir), 1):
        print(f"  {index}. {step}")
    return 0
