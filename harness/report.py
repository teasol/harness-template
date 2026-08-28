"""JSON + Markdown report generation for harness runs."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from harness.runner import RunResult


def write_reports(result: RunResult) -> tuple[Path, Path]:
    """Write ``report.json`` and ``report.md`` into the run directory."""
    run_dir = Path(result.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    json_path = run_dir / "report.json"
    md_path = run_dir / "report.md"

    json_path.write_text(
        json.dumps(dataclasses.asdict(result), indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_markdown(result), encoding="utf-8")
    return json_path, md_path


def _markdown(result: RunResult) -> str:
    status = "PASSED" if result.success else "FAILED"
    lines = [
        f"# Harness report: {result.spec_name}",
        "",
        f"**Status: {status}**",
        "",
        f"- Started: {result.started_at}",
        f"- Finished: {result.finished_at}",
        f"- Run dir: `{result.run_dir}`",
        "",
        "| Step | Exit | Duration (s) | Checks | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for step in result.steps:
        passed = sum(1 for c in step.checks if c.passed)
        total = len(step.checks)
        exit_col = "timeout" if step.exit_code is None else str(step.exit_code)
        mark = "pass" if step.success else "FAIL"
        lines.append(
            f"| `{step.step_id}` | {exit_col} | {step.duration_s:.2f} | {passed}/{total} | {mark} |"
        )

    failed = [c for s in result.steps for c in s.checks if not c.passed]
    if failed:
        lines += ["", "## Failed checks", ""]
        for check in failed:
            lines.append(f"- `{check.check_type}`: {check.detail}")

    lines.append("")
    return "\n".join(lines)
