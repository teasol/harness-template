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
    python -m harness exp start <name> [--base main] [--path DIR]
    python -m harness exp list
    python -m harness exp report <name> [--no-run] [--determinism] [--save]
    python -m harness exp remove <name> [--force]
    python -m harness planner brief <name> [--register <label>]

"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from harness import experiment as exp_mod
from harness import task as task_mod
from harness.experiment import ExperimentError
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
    write_worker_config,
)
from harness.spec import SpecError, load_spec
from harness.task import TaskError
from harness.worker import (
    WorkerError,
    load_worker_config,
    run_task,
    write_worker_report,
)


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


def cmd_plan_check(args: argparse.Namespace) -> int:
    """Validate every plan and flag drift — with no plan name hardcoded.

    Lets the Makefile, pre-commit, and CI gate a project's plans without
    naming one, so removing the shipped demo (or adding a plan) needs no
    edits to any of them.
    """
    plans = sorted(Path(args.plans_dir).glob("*.yaml"))
    if not plans:
        print(f"(no plans in {args.plans_dir})")
        return 0
    problems = 0
    for path in plans:
        try:
            plan = load_plan(path)
        except PlanError as exc:
            print(f"  INVALID {path}: {exc}", file=sys.stderr)
            problems += 1
            continue
        drift = task_mod.spec_drift(plan, args.tasks_dir)
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
    try:
        plan = load_plan(args.plan)
    except PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    board = {t.id: t for t in task_mod.load_board(args.tasks_dir)}
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
    if args.plan:
        board = [t for t in board if t.plan == args.plan]
        if not board:
            print(f"(no tasks for plan '{args.plan}')")
            return 0
    ready = set(task_mod.ready_task_ids(task_mod.load_board(args.tasks_dir)))
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
    plan_run.set_defaults(func=cmd_plan_run)

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

    exp_remove = exp_sub.add_parser("remove", help="Remove an experiment worktree (keeps branch)")
    exp_remove.add_argument("name", help="Experiment name")
    exp_remove.add_argument("--force", action="store_true", help="Remove even if dirty")
    exp_remove.set_defaults(func=cmd_exp_remove)

    for exp_parser in (exp_start, exp_list, exp_report, exp_remove):
        exp_parser.add_argument("--root", default=".", help="Repo root (default: cwd)")

    status_cmd = sub.add_parser("status", help="Where am I and what do I do next? (start here)")
    status_cmd.add_argument("--root", default=".", help="Repo root (default: cwd)")
    status_cmd.set_defaults(func=cmd_status)

    setup_cmd = sub.add_parser(
        "setup", help="Choose the Worker platform, model, and reasoning level"
    )
    setup_cmd.add_argument("--platform", default=None, help="Platform name (see --list)")
    setup_cmd.add_argument("--model", default=None, help="Model to run Workers on")
    setup_cmd.add_argument("--effort", default=None, help="Reasoning level")
    setup_cmd.add_argument("--command", default=None, help="Override the platform's command")
    setup_cmd.add_argument("--attempts", type=int, default=6, help="Retry cap per task")
    setup_cmd.add_argument("--label", default="worker", help="Name recorded on the board")
    setup_cmd.add_argument("--list", action="store_true", help="List platforms and exit")
    setup_cmd.add_argument("--platforms", default=None, help="Platform presets YAML")
    setup_cmd.add_argument("--out", default=None, help="Where to write the worker config")
    setup_cmd.add_argument("--root", default=".", help="Repo root (default: cwd)")
    setup_cmd.set_defaults(func=cmd_setup)

    planner_cmd = sub.add_parser("planner", help="Planner registration")
    planner_sub = planner_cmd.add_subparsers(dest="planner_command", required=True)
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
    planner_brief.add_argument("--root", default=".", help="Repo root (default: cwd)")
    planner_brief.set_defaults(func=cmd_planner_brief)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


# ---------------------------------------------------------------------------
# Experiments (researcher <-> Planner)


def cmd_exp_start(args: argparse.Namespace) -> int:
    try:
        experiment = exp_mod.start(
            args.name, root=args.root, worktree_root=args.path, base=args.base
        )
    except ExperimentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"experiment '{experiment.name}' created")
    print(f"  branch:   {experiment.branch}")
    print(f"  worktree: {experiment.path}")
    print(f"  plan:     {experiment.plan_path}")
    print()
    print("Next (Planner, from inside the worktree):")
    print(f"  cd {experiment.path}")
    print(f"  # fill in plans/{experiment.name}.yaml, then:")
    print(f"  python -m harness plan validate plans/{experiment.name}.yaml")
    print(f"  python -m harness plan materialize plans/{experiment.name}.yaml")
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
    try:
        config = load_worker_config(args.worker_config, root=args.root)
        if args.attempts is not None:
            config.attempts = args.attempts
        if args.adapter:
            config.adapter = args.adapter
        outcome = run_task(
            args.tasks_dir,
            args.id,
            config=config,
            root=args.root,
            results_dir=args.results_dir,
            worker_name=args.by,
        )
    except (TaskError, WorkerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write_worker_report(outcome, args.results_dir, root=args.root)
    print(f"[{outcome.task_id}] {outcome.status.upper()}")
    _print_worker_outcome(outcome)
    return 0 if outcome.succeeded else 1


def cmd_plan_run(args: argparse.Namespace) -> int:
    """Drain the ready queue: run each ready task in dependency order."""
    try:
        plan = load_plan(args.plan)
        config = load_worker_config(args.worker_config, root=args.root)
    except (PlanError, WorkerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.attempts is not None:
        config.attempts = args.attempts

    if config.adapter == "manual":
        print(
            "worker adapter is 'manual': each task writes a briefing and stops.\n"
            "Set `adapter: cli` in configs/worker.yaml to have Workers built here.\n"
        )

    completed = 0
    while True:
        board = task_mod.load_board(args.tasks_dir)
        ready = task_mod.ready_task_ids(board)
        if not ready:
            break
        task_id = ready[0]
        print(f"--- running task '{task_id}' ---")
        try:
            outcome = run_task(
                args.tasks_dir,
                task_id,
                config=config,
                root=args.root,
                results_dir=args.results_dir,
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
        print("no ready tasks left — remaining modules are blocked or unmaterialized")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Planner registration


def cmd_planner_brief(args: argparse.Namespace) -> int:
    try:
        if args.register:
            exp_mod.register_planner(
                args.name,
                args.register,
                root=args.root,
                model=args.model or "",
                effort=args.effort or "",
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
    print(f"Project: {status.project_name}")
    if status.demo_present:
        print("  (the shipped demo is here — `instantiate.py --exam-demo` runs it)")
    print()
    print(status.headline)

    if status.experiments:
        print()
        print("  EXPERIMENT        STATE              DETAIL")
        for exp in status.experiments:
            marker = "*" if exp.name == status.here else " "
            print(f" {marker}{exp.name:<17} {exp.state:<18} {exp.detail}")

    if status.worker_tier.get("adapter") == "cli":
        print()
        tier = status.worker_tier
        shown = " · ".join(
            x for x in (tier.get("platform"), tier.get("model"), tier.get("effort")) if x
        )
        print(f"  Workers run on: {shown or 'cli'}")
    if status.worker_adapter == "manual" and status.experiments:
        print()
        print(
            "  Workers are MANUAL: `plan run` writes a briefing and stops rather\n"
            "  than building anything. Run `harness setup` to pick a platform,\n"
            "  model, and reasoning level, and Workers will be spawned for you."
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


def cmd_setup(args: argparse.Namespace) -> int:
    try:
        platforms = load_platforms(args.platforms, root=args.root)
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list:
        print("Worker platforms (from the presets file — edit it to add your own):\n")
        for platform in platforms.values():
            print(f"  {platform.name:<10} {platform.label}")
            if platform.efforts:
                print(f"  {'':<10} reasoning levels: {', '.join(platform.efforts)}")
            if platform.model_hint:
                print(f"  {'':<10} model: {platform.model_hint}")
            if platform.docs:
                print(f"  {'':<10} check flags with: {platform.docs}")
            print()
        return 0

    name = args.platform
    if not name:
        print("Tier 3 — the Workers that write module code.\n")
        print("Their work is bounded and specified, so a small fast model is usually")
        print("right; keep the expensive one for planning. Available platforms:\n")
        for platform in platforms.values():
            print(f"  {platform.name:<10} {platform.label}")
        print()
        name = _prompt("Platform", next(iter(platforms)))

    platform = platforms.get(name)
    if platform is None:
        print(
            f"error: unknown platform '{name}'. available: {', '.join(platforms)}",
            file=sys.stderr,
        )
        return 2

    model = args.model
    if model is None:
        model = _prompt(f"Model ({platform.model_hint})", platform.default_model)
    effort = args.effort
    if effort is None:
        effort = _prompt("Reasoning level", platform.default_effort, platform.efforts or None)
    command = args.command
    if command is None and platform.is_custom:
        command = _prompt("Command (takes the briefing, edits {root}, exits)", "")

    try:
        config = build_config(
            platform,
            model=model,
            effort=effort,
            attempts=args.attempts,
            command=command,
            label=args.label,
        )
        path = write_worker_config(config, args.out, root=args.root)
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"\nwrote {path}")
    print(f"  platform: {config.platform}")
    print(f"  model:    {config.model or '(platform default)'}")
    print(f"  effort:   {config.effort or '(platform default)'}")
    print(f"  attempts: {config.attempts}")
    print(f"  command:  {config.command}")
    if platform.docs:
        print(f"\nFlags change between releases — verify with: {platform.docs}")
    print("\nWorkers will now be spawned by `harness plan run`.")
    return 0
