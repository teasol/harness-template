"""Tests for the command-line interface.

The CLI is the harness's actual contract with agents and CI: they consume its
exit codes, not its Python API. Exit codes are therefore asserted explicitly —
``0`` success, ``1`` verification failed, ``2`` usage/spec error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.cli import main

NONDET_SPEC = """
name: nondet
steps:
  - id: roll
    run: >-
      $HARNESS_PYTHON -c "import random, json, os;
      open(os.environ['HARNESS_RESULTS_DIR'] + '/roll.json', 'w').write(
      json.dumps({'v': random.SystemRandom().random()}))"
"""

FAILING_SPEC = """
name: failing
steps:
  - id: boom
    run: "false"
"""


@pytest.fixture()
def results(tmp_path: Path) -> str:
    return str(tmp_path / "results")


# ---------------------------------------------------------------------------
# verify / hash


def test_verify_success_returns_zero(results: str, capsys) -> None:
    assert main(["verify", "--spec", "configs/demo.yaml", "--results-dir", results]) == 0
    assert "PASSED" in capsys.readouterr().out


def test_verify_failure_returns_one(tmp_path: Path, results: str) -> None:
    spec = tmp_path / "failing.yaml"
    spec.write_text(FAILING_SPEC, encoding="utf-8")
    assert main(["verify", "--spec", str(spec), "--results-dir", results]) == 1


def test_verify_missing_spec_returns_two(results: str, capsys) -> None:
    assert main(["verify", "--spec", "configs/nope.yaml", "--results-dir", results]) == 2
    assert "not found" in capsys.readouterr().err


def test_hash_prints_digest(tmp_path: Path, capsys) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello\n", encoding="utf-8")
    assert main(["hash", str(target)]) == 0
    digest = capsys.readouterr().out.split()[0]
    assert len(digest) == 64


def test_hash_missing_file_returns_two(tmp_path: Path) -> None:
    assert main(["hash", str(tmp_path / "ghost.txt")]) == 2


# ---------------------------------------------------------------------------
# reproduce (the determinism gate)


def test_reproduce_deterministic_spec_returns_zero(results: str, capsys) -> None:
    assert main(["reproduce", "--spec", "configs/demo.yaml", "--results-dir", results]) == 0
    out = capsys.readouterr().out
    assert "REPRODUCIBLE" in out and "output.json" in out
    payload = json.loads((Path(results) / "reproduce.json").read_text(encoding="utf-8"))
    assert payload["reproducible"] is True
    assert payload["times"] == 2


def test_reproduce_detects_nondeterminism(tmp_path: Path, results: str, capsys) -> None:
    spec = tmp_path / "nondet.yaml"
    spec.write_text(NONDET_SPEC, encoding="utf-8")
    assert main(["reproduce", "--spec", str(spec), "--results-dir", results]) == 1
    assert "NOT REPRODUCIBLE" in capsys.readouterr().err


def test_reproduce_rejects_specs_with_no_artifacts(tmp_path: Path, results: str, capsys) -> None:
    """A gate comparing zero files would pass unconditionally — refuse it."""
    spec = tmp_path / "empty.yaml"
    spec.write_text("name: empty\nsteps:\n  - id: noop\n    run: 'true'\n", encoding="utf-8")
    assert main(["reproduce", "--spec", str(spec), "--results-dir", results]) == 2
    assert "no comparable artifacts" in capsys.readouterr().err


def test_reproduce_requires_at_least_two_runs(results: str) -> None:
    assert (
        main(["reproduce", "--spec", "configs/demo.yaml", "--times", "1", "--results-dir", results])
        == 2
    )


def test_reproduce_reports_failing_spec_as_usage_error(
    tmp_path: Path, results: str, capsys
) -> None:
    spec = tmp_path / "failing.yaml"
    spec.write_text(FAILING_SPEC, encoding="utf-8")
    assert main(["reproduce", "--spec", str(spec), "--results-dir", results]) == 2
    assert "failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# plan


def test_plan_validate(capsys) -> None:
    assert main(["plan", "validate", "plans/demo-pipeline.yaml"]) == 0
    assert "valid" in capsys.readouterr().out


def test_plan_validate_bad_plan_returns_two(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("plan:\n  name: x\n", encoding="utf-8")  # no goal, no modules
    assert main(["plan", "validate", str(bad)]) == 2


def test_plan_materialize_and_status(tmp_path: Path, capsys) -> None:
    tasks = str(tmp_path / "tasks")
    assert main(["plan", "materialize", "plans/demo-pipeline.yaml", "--tasks-dir", tasks]) == 0
    assert "wrote" in capsys.readouterr().out
    # Second run is a no-op without --force.
    assert main(["plan", "materialize", "plans/demo-pipeline.yaml", "--tasks-dir", tasks]) == 0
    assert "already exist" in capsys.readouterr().out
    assert main(["plan", "status", "plans/demo-pipeline.yaml", "--tasks-dir", tasks]) == 0
    assert "Progress: 0/2 done" in capsys.readouterr().out


def test_plan_status_check_detects_drift(tmp_path: Path, capsys) -> None:
    """A task file left behind by a plan edit must fail the check, not pass quietly."""
    import yaml

    tasks_dir = tmp_path / "tasks"
    tasks = str(tasks_dir)
    status_check = ["plan", "status", "plans/demo-pipeline.yaml", "--tasks-dir", tasks, "--check"]
    main(["plan", "materialize", "plans/demo-pipeline.yaml", "--tasks-dir", tasks])
    assert main(status_check) == 0

    stale = tasks_dir / "stats.task.yaml"
    data = yaml.safe_load(stale.read_text(encoding="utf-8"))
    data["task"]["brief"] = "instructions from a plan revision that no longer exists"
    stale.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    assert main(status_check) == 1
    out = capsys.readouterr().out
    assert "Drift" in out and "stats: brief" in out


# ---------------------------------------------------------------------------
# task


@pytest.fixture()
def tasks_dir(tmp_path: Path) -> str:
    path = str(tmp_path / "tasks")
    main(["plan", "materialize", "plans/demo-pipeline.yaml", "--tasks-dir", path])
    return path


def test_task_list_and_show(tasks_dir: str, capsys) -> None:
    assert main(["task", "list", "--tasks-dir", tasks_dir]) == 0
    out = capsys.readouterr().out
    assert "data-gen" in out and "READY" in out

    assert main(["task", "show", "--id", "stats", "--tasks-dir", tasks_dir]) == 0
    assert "depends_on" in capsys.readouterr().out


def test_task_list_empty_board(tmp_path: Path, capsys) -> None:
    assert main(["task", "list", "--tasks-dir", str(tmp_path / "none")]) == 0
    assert "(no tasks)" in capsys.readouterr().out


def test_task_show_unknown_id_returns_two(tasks_dir: str) -> None:
    assert main(["task", "show", "--id", "ghost", "--tasks-dir", tasks_dir]) == 2


def test_task_claim_blocks_on_unready_dependency(tasks_dir: str, capsys) -> None:
    assert main(["task", "claim", "--id", "stats", "--by", "w1", "--tasks-dir", tasks_dir]) == 2
    assert "dependencies not done" in capsys.readouterr().err
    assert (
        main(["task", "claim", "--id", "stats", "--by", "w1", "--force", "--tasks-dir", tasks_dir])
        == 0
    )


def test_task_claim_block_roundtrip(tasks_dir: str, capsys) -> None:
    assert main(["task", "claim", "--id", "data-gen", "--by", "w1", "--tasks-dir", tasks_dir]) == 0
    assert "in_progress" in capsys.readouterr().out
    assert (
        main(["task", "block", "--id", "data-gen", "--reason", "unclear", "--tasks-dir", tasks_dir])
        == 0
    )
    assert "blocked" in capsys.readouterr().out


def test_task_verify_requires_id_or_all(tasks_dir: str, capsys) -> None:
    assert main(["task", "verify", "--tasks-dir", tasks_dir]) == 2
    assert "--id" in capsys.readouterr().err


def test_task_verify_all(tasks_dir: str, results: str, capsys) -> None:
    code = main(["task", "verify", "--all", "--tasks-dir", tasks_dir, "--results-dir", results])
    out = capsys.readouterr().out
    assert code == 0
    assert "2/2 task(s) passed" in out


def test_task_verify_all_with_status_filter(tasks_dir: str, results: str, capsys) -> None:
    code = main(
        [
            "task",
            "verify",
            "--all",
            "--status",
            "done",
            "--tasks-dir",
            tasks_dir,
            "--results-dir",
            results,
        ]
    )
    assert code == 0
    assert "(no matching tasks)" in capsys.readouterr().out


def test_task_done_marks_done(tasks_dir: str, results: str, capsys) -> None:
    main(["task", "claim", "--id", "data-gen", "--by", "w1", "--tasks-dir", tasks_dir])
    capsys.readouterr()
    code = main(
        [
            "task",
            "done",
            "--id",
            "data-gen",
            "--by",
            "w1",
            "--tasks-dir",
            tasks_dir,
            "--results-dir",
            results,
        ]
    )
    assert code == 0
    assert "→ done" in capsys.readouterr().out


def test_task_done_fails_on_missing_deliverable(tasks_dir: str, results: str, capsys) -> None:
    import yaml

    path = Path(tasks_dir) / "data-gen.task.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["task"]["deliverables"].append("src/demo_pipeline/ghost.py")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    code = main(
        ["task", "done", "--id", "data-gen", "--tasks-dir", tasks_dir, "--results-dir", results]
    )
    assert code == 1
    assert "deliverable missing" in capsys.readouterr().out
    # Status must not advance on a failed acceptance.
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["task"]["status"] == "todo"


def test_unknown_command_exits_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["frobnicate"])
    assert excinfo.value.code == 2
