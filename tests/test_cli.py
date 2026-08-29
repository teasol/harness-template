"""Tests for the command-line interface.

The CLI is the harness's actual contract with agents and CI: they consume its
exit codes, not its Python API. Exit codes are therefore asserted explicitly —
``0`` success, ``1`` verification failed, ``2`` usage/spec error.
"""

from __future__ import annotations

import json
import shutil
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


# ---------------------------------------------------------------------------
# plan check: gates a project's plans without naming one


def test_plan_check_passes_on_the_shipped_plan(capsys) -> None:
    assert main(["plan", "check"]) == 0
    assert "demo-pipeline.yaml" in capsys.readouterr().out


def test_plan_check_on_an_empty_project(tmp_path: Path, capsys) -> None:
    """A fresh project has no plans yet; that is not a failure."""
    assert main(["plan", "check", "--plans-dir", str(tmp_path / "plans")]) == 0
    assert "no plans" in capsys.readouterr().out


def test_plan_check_reports_drift(tmp_path: Path, capsys) -> None:
    import yaml

    plans = tmp_path / "plans"
    plans.mkdir()
    tasks = tmp_path / "tasks"
    shutil.copy("plans/demo-pipeline.yaml", plans / "demo-pipeline.yaml")
    main(["plan", "materialize", str(plans / "demo-pipeline.yaml"), "--tasks-dir", str(tasks)])
    capsys.readouterr()
    assert main(["plan", "check", "--plans-dir", str(plans), "--tasks-dir", str(tasks)]) == 0

    stale = tasks / "stats.task.yaml"
    data = yaml.safe_load(stale.read_text(encoding="utf-8"))
    data["task"]["brief"] = "an instruction the plan no longer gives"
    stale.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    assert main(["plan", "check", "--plans-dir", str(plans), "--tasks-dir", str(tasks)]) == 1
    assert "DRIFT" in capsys.readouterr().err


def test_plan_status_counts_only_this_plans_modules(tmp_path: Path, capsys) -> None:
    """Foreign task files must not be counted as this plan's progress."""
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    # A finished task belonging to some other plan.
    (tasks / "stranger.task.yaml").write_text(
        "task:\n  id: stranger\n  plan: elsewhere\n  status: done\n  acceptance:\n    steps: []\n",
        encoding="utf-8",
    )
    assert main(["plan", "status", "plans/demo-pipeline.yaml", "--tasks-dir", str(tasks)]) == 0
    out = capsys.readouterr().out
    assert "Progress: 0/2 done" in out
    assert "not in this plan" in out


def test_task_list_can_filter_by_plan(tasks_dir: str, capsys) -> None:
    assert main(["task", "list", "--tasks-dir", tasks_dir, "--plan", "demo-pipeline"]) == 0
    assert "data-gen" in capsys.readouterr().out
    assert main(["task", "list", "--tasks-dir", tasks_dir, "--plan", "nope"]) == 0
    assert "no tasks for plan" in capsys.readouterr().out


def test_status_command_runs_and_points_somewhere(capsys) -> None:
    """`harness status` is the documented entry point; it must always advise."""
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Project:" in out
    assert "Next:" in out
    assert "README.md" in out


# ---------------------------------------------------------------------------
# setup: choosing the Worker tier


def test_setup_lists_platforms(capsys) -> None:
    assert main(["setup", "--list"]) == 0
    out = capsys.readouterr().out
    assert "claude" in out and "reasoning levels" in out


def test_setup_writes_both_tiers(tmp_path: Path, capsys) -> None:
    """Planner and Worker are configured together, so the split is deliberate."""
    shutil.copytree("configs", tmp_path / "configs")
    code = main(
        [
            "setup",
            "--root",
            str(tmp_path),
            "--planner-platform",
            "claude",
            "--planner-model",
            "opus",
            "--planner-effort",
            "high",
            "--worker-platform",
            "claude",
            "--worker-model",
            "haiku",
            "--worker-effort",
            "low",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "planner  claude · opus · high" in out
    assert "worker   claude · haiku · low" in out

    from harness.worker import load_agent_config

    assert load_agent_config("planner", root=tmp_path).model == "opus"
    assert load_agent_config("worker", root=tmp_path).model == "haiku"


def test_setup_writes_manual_tiers(tmp_path: Path, capsys) -> None:
    shutil.copytree("configs", tmp_path / "configs")
    code = main(
        [
            "setup",
            "--root",
            str(tmp_path),
            "--planner-platform",
            "manual",
            "--worker-platform",
            "manual",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "planner  manual" in out
    assert "worker   manual" in out

    from harness.worker import load_agent_config

    assert load_agent_config("planner", root=tmp_path).adapter == "manual"
    assert load_agent_config("worker", root=tmp_path).adapter == "manual"


def test_setup_can_attach_a_session(tmp_path: Path, capsys) -> None:
    shutil.copytree("configs", tmp_path / "configs")
    code = main(
        [
            "setup",
            "--root",
            str(tmp_path),
            "--planner-platform",
            "claude",
            "--planner-model",
            "opus",
            "--planner-effort",
            "high",
            "--planner-session",
            "sess-42",
            "--worker-platform",
            "claude",
            "--worker-model",
            "haiku",
            "--worker-effort",
            "low",
        ]
    )
    assert code == 0
    assert "attached to session sess-42" in capsys.readouterr().out

    from harness.worker import load_agent_config

    planner = load_agent_config("planner", root=tmp_path)
    assert planner.session == "sess-42" and "{session}" in planner.command


def test_setup_rejects_an_unknown_platform(tmp_path: Path, capsys) -> None:
    shutil.copytree("configs", tmp_path / "configs")
    code = main(["setup", "--root", str(tmp_path), "--worker-platform", "nope"])
    assert code == 2
    assert "unknown platform" in capsys.readouterr().err


def test_setup_rejects_an_unknown_reasoning_level(tmp_path: Path, capsys) -> None:
    shutil.copytree("configs", tmp_path / "configs")
    code = main(
        [
            "setup",
            "--root",
            str(tmp_path),
            "--worker-platform",
            "claude",
            "--worker-model",
            "haiku",
            "--worker-effort",
            "turbo",
        ]
    )
    assert code == 2
    assert "not a reasoning level" in capsys.readouterr().err


def test_exp_question_records_and_reads_back(tmp_path: Path, capsys) -> None:
    """The conversational path: open first, agree on the question, record it."""
    import subprocess

    clone = tmp_path / "repo"
    subprocess.run(["git", "clone", "--quiet", ".", str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "t@e.invalid"], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "t"], check=True)

    assert main(["exp", "start", "conv", "--root", str(clone)]) == 0
    capsys.readouterr()

    # Nothing recorded yet — and the command says so rather than printing blank.
    assert main(["exp", "question", "conv", "--root", str(clone)]) == 1
    assert "no question recorded yet" in capsys.readouterr().out

    assert main(["exp", "question", "conv", "--set", "Is it faster?", "--root", str(clone)]) == 0
    capsys.readouterr()
    assert main(["exp", "question", "conv", "--root", str(clone)]) == 0
    assert "Is it faster?" in capsys.readouterr().out
