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
    run: $HARNESS_PYTHON -c "import time; time.sleep(5)"
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


def test_runner_exports_python_and_seed(tmp_path: Path) -> None:
    """Steps get a usable interpreter path and the spec seed, not a bare `python`."""
    spec_path = tmp_path / "env.yaml"
    spec_path.write_text(
        """
name: env-probe
seed: 7
steps:
  - id: probe
    run: "$HARNESS_PYTHON -c \\"import os;print(os.environ['HARNESS_SEED'])\\" > seed.txt"
    checks:
      - type: text_contains
        path: seed.txt
        contains: "7"
""",
        encoding="utf-8",
    )
    spec = load_spec(spec_path)
    runner = Runner(root=str(tmp_path), results_dir=tmp_path / "results")
    result = runner.run(spec)
    assert result.success, [c.detail for s in result.steps for c in s.checks]


def test_report_records_provenance(tmp_path: Path) -> None:
    """A report must answer 'what produced this?' — commit, interpreter, seed."""
    spec = load_spec("configs/demo.yaml")
    runner = Runner(root=".", results_dir=tmp_path / "results")
    result = runner.run(spec)

    prov = result.provenance
    assert prov["seed"] == 42
    assert prov["python_version"]
    assert prov["platform"]
    assert prov["harness_version"]
    assert "git_commit" in prov and "git_dirty" in prov

    _, md_report = write_reports(result)
    text = md_report.read_text(encoding="utf-8")
    assert "## Provenance" in text
    payload = json.loads((Path(result.run_dir) / "report.json").read_text(encoding="utf-8"))
    assert payload["provenance"]["seed"] == 42


# ---------------------------------------------------------------------------
# seeding must not silently redefine the quantity being measured


def _env_probe_spec(tmp_path: Path, body: str) -> Path:
    spec_path = tmp_path / "probe.yaml"
    spec_path.write_text(body, encoding="utf-8")
    return spec_path


def test_seed_alone_does_not_inject_math_env(tmp_path: Path) -> None:
    """`seed:` seeds. It must not change which GPU kernels get selected.

    CUBLAS_WORKSPACE_CONFIG constrains cuBLAS algorithm choice, so applying it
    behind a plain `seed:` would make every harness measurement incomparable to
    the project's own historical numbers — a reproduction could fail, or worse
    pass, for a reason nobody declared.
    """
    spec_path = _env_probe_spec(
        tmp_path,
        """
name: probe
seed: 42
steps:
  - id: probe
    run: 'echo "hash=${PYTHONHASHSEED:-unset} cublas=${CUBLAS_WORKSPACE_CONFIG:-unset}"'
    checks: []
""",
    )
    result = Runner(root=".", results_dir=tmp_path / "results").run(load_spec(spec_path))
    assert result.success
    log = Path(result.steps[0].log_path).read_text(encoding="utf-8")
    assert "hash=42" in log
    assert "cublas=unset" in log
    assert result.provenance["injected_env"] == {"PYTHONHASHSEED": "42"}
    assert result.provenance["deterministic_math"] is False


def test_deterministic_math_is_opt_in_and_recorded(tmp_path: Path) -> None:
    spec_path = _env_probe_spec(
        tmp_path,
        """
name: probe
seed: 7
deterministic_math: true
steps:
  - id: probe
    run: 'echo "cublas=${CUBLAS_WORKSPACE_CONFIG:-unset}"'
    checks: []
""",
    )
    result = Runner(root=".", results_dir=tmp_path / "results").run(load_spec(spec_path))
    assert result.success
    assert "cublas=:4096:8" in Path(result.steps[0].log_path).read_text(encoding="utf-8")
    assert result.provenance["injected_env"]["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"

    # And it must be impossible to read the report without seeing it.
    _, md_report = write_reports(result)
    text = md_report.read_text(encoding="utf-8")
    assert "Deterministic math is ON" in text
    assert "CUBLAS_WORKSPACE_CONFIG" in text


def test_steps_run_under_bash_not_dash(tmp_path: Path) -> None:
    """Acceptance steps are written by humans and agents who assume bash.

    `subprocess.run(shell=True)` uses /bin/sh, which on Debian/Ubuntu is dash —
    so `set -o pipefail`, `[[ ]]` and arrays all fail with an error that names
    the shell rather than the step, and nothing in the spec schema hints that
    POSIX sh was the contract. Pinning bash removes the whole class.
    """
    spec_path = tmp_path / "bashism.yaml"
    spec_path.write_text(
        """
name: bashism
steps:
  - id: pipefail
    run: |
      set -euo pipefail
      arr=(a b c)
      [[ ${#arr[@]} -eq 3 ]] && echo "bash ok"
    checks: []
""",
        encoding="utf-8",
    )
    result = Runner(root=".", results_dir=tmp_path / "results").run(load_spec(spec_path))
    assert result.success, Path(result.steps[0].log_path).read_text(encoding="utf-8")
    assert "bash ok" in Path(result.steps[0].log_path).read_text(encoding="utf-8")
