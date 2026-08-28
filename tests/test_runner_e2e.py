"""End-to-end tests for the runner, reports, and the shipped demo spec."""

from __future__ import annotations

import json
from pathlib import Path

from harness.report import write_reports
from harness.runner import Runner
from harness.spec import load_spec


def test_demo_spec_end_to_end(tmp_path: Path) -> None:
    """Run the shipped demo spec against the real repo root."""
    spec = load_spec("configs/demo.yaml")
    runner = Runner(root=".", results_dir=tmp_path / "results")
    result = runner.run(spec)

    assert result.success, [c.detail for s in result.steps for c in s.checks]
    assert len(result.steps) == 2

    run_dir = Path(result.run_dir)
    output = run_dir / "output.json"
    assert output.is_file()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["seed"] == 42
    assert 0 <= payload["value"] <= 100

    json_report, md_report = write_reports(result)
    assert json_report.is_file() and md_report.is_file()
    assert "# Harness report: demo" in md_report.read_text(encoding="utf-8")
    # Step logs are captured.
    logs = list((run_dir / "logs").glob("*.log"))
    assert len(logs) == 2


def test_failing_step_stops_run(tmp_path: Path) -> None:
    spec_path = tmp_path / "fail.yaml"
    spec_path.write_text(
        """
name: fail-case
steps:
  - id: boom
    run: "false"
    checks:
      - type: file_exists
        path: never-created.txt
  - id: never-reached
    run: echo should-not-run
""",
        encoding="utf-8",
    )
    spec = load_spec(spec_path)
    runner = Runner(root=str(tmp_path), results_dir=tmp_path / "results")
    result = runner.run(spec)

    assert not result.success
    assert len(result.steps) == 1  # stop_on_failure: second step never ran
    failed_checks = [c for c in result.steps[0].checks if not c.passed]
    assert failed_checks and "exited with code" in failed_checks[0].detail


def test_determinism_two_runs_same_hash(tmp_path: Path) -> None:
    """Two runs of the demo step must produce identical output hashes."""
    spec = load_spec("configs/demo.yaml")
    hashes = []
    for i in range(2):
        runner = Runner(root=".", results_dir=tmp_path / f"results-{i}")
        result = runner.run(spec)
        assert result.success
        output = Path(result.run_dir) / "output.json"
        hashes.append(json.loads(output.read_text(encoding="utf-8"))["value"])
    assert hashes[0] == hashes[1]


def test_timeout(tmp_path: Path) -> None:
    spec_path = tmp_path / "slow.yaml"
    spec_path.write_text(
        """
name: slow
steps:
  - id: sleep
    run: python -c "import time; time.sleep(5)"
    timeout: 1
""",
        encoding="utf-8",
    )
    spec = load_spec(spec_path)
    runner = Runner(root=str(tmp_path), results_dir=tmp_path / "results")
    result = runner.run(spec)
    assert not result.success
    assert result.steps[0].exit_code is None
    assert any(c.check_type == "timeout" for c in result.steps[0].checks)
