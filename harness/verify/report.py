"""JSON + Markdown report for one pass over a checklist."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from harness.verify.checklist import ChecklistRun


class MetricError(ValueError):
    """Raised when a declared metric cannot be read out of an artifact."""


def lookup_metric(data: object, metric: str) -> object:
    """Resolve a dotted path inside a loaded JSON document.

    A plan's `report:` block declares *where* each number lives rather than
    what it is, so this is how the harness reads one out. It used to belong to
    the `json_metric` check type; extracting a value for a report outlived
    asserting things about it.
    """
    node: object = data
    for part in metric.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise MetricError(f"metric path '{metric}' not found in JSON document")
    return node


def write_reports(run: ChecklistRun) -> tuple[Path, Path]:
    """Write ``report.json`` and ``report.md`` into the run directory."""
    run_dir = Path(run.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    json_path = run_dir / "report.json"
    md_path = run_dir / "report.md"
    payload = dataclasses.asdict(run)
    # `passed` is a property, and a reader of the JSON should not have to
    # recompute the verdict the harness already reached.
    payload["passed"] = run.passed
    for entry, result in zip(payload["results"], run.results, strict=True):
        entry["passed"] = result.passed

    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(run), encoding="utf-8")
    return json_path, md_path


def _provenance_lines(run: ChecklistRun) -> list[str]:
    """The 'how do I redo this?' block, if provenance was captured."""
    prov = run.provenance
    if not prov:
        return []
    commit = prov.get("git_commit") or "unknown"
    if prov.get("git_dirty"):
        commit = f"{commit} (dirty worktree — uncommitted changes)"
    return [
        "## Provenance",
        "",
        f"- Commit: `{commit}`",
        f"- Git branch: `{prov.get('git_branch') or 'unknown'}`",
        f"- Python: {prov.get('python_version') or 'unknown'} (`{prov.get('python_executable')}`)",
        f"- Platform: {prov.get('platform') or 'unknown'}",
        f"- Harness: {prov.get('harness_version') or 'unknown'}",
        "",
    ]


def _markdown(run: ChecklistRun) -> str:
    lines = [
        f"# Checklist: {run.scope or 'plan'}",
        "",
        f"**{'PASSED' if run.passed else 'FAILED'}** — "
        f"{sum(1 for r in run.results if r.passed)}/{len(run.results)} item(s) passing",
        "",
        f"- Started: {run.started_at}",
        f"- Finished: {run.finished_at}",
        f"- Run dir: `{run.run_dir}`",
        "",
    ]
    lines += _provenance_lines(run)
    lines += [
        "| Item | Command | Exit | Duration (s) | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in run.results:
        exit_col = "timeout" if result.timed_out else str(result.exit_code)
        lines.append(
            f"| `{result.ref}` | `{result.command}` | {exit_col} | "
            f"{result.duration_s:.2f} | {'pass' if result.passed else 'FAIL'} |"
        )

    if run.failures:
        lines += ["", "## Failing items", ""]
        for result in run.failures:
            lines += [
                f"- **`{result.ref}`** — {result.detail}",
                f"  - command: `{result.command}`",
                f"  - log: `{result.log_path}`",
            ]
    if not run.results:
        lines += [
            "",
            "No items ran. An empty checklist establishes nothing, so this is a",
            "failure rather than a pass.",
        ]

    lines.append("")
    return "\n".join(lines)
