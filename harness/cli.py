"""Command-line interface for the harness.

Usage::

    python -m harness verify --spec configs/demo.yaml [--results-dir DIR]
    python -m harness hash <file> [<file> ...]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from harness.report import write_reports
from harness.reproducibility import file_sha256
from harness.runner import Runner
from harness.spec import SpecError, load_spec


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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
