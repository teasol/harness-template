"""Determinism gate: run a spec repeatedly and compare what it produced.

``make reproduce`` used to re-run a spec and compare nothing, which proves
nothing. This module runs a spec N times and diffs a manifest of every
artifact each run wrote, so a divergence is a failure rather than a shrug.

Harness bookkeeping (``report.json``, ``report.md``, ``logs/``) is excluded
from the manifest: it records timestamps and durations, which differ between
runs by construction. What remains is exactly the run's research output.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from harness.verify.report import write_reports
from harness.verify.reproducibility import collect_provenance, file_sha256
from harness.verify.runner import Runner
from harness.verify.spec import Spec

#: Files the harness itself writes; never determinism-stable, never compared.
BOOKKEEPING = frozenset({"report.json", "report.md", "reproduce.json"})
BOOKKEEPING_DIRS = frozenset({"logs"})


class ReproduceError(RuntimeError):
    """Raised when a reproducibility run cannot be carried out at all."""


@dataclasses.dataclass
class ReproduceResult:
    """Outcome of comparing N runs of the same spec."""

    spec_name: str
    times: int
    reproducible: bool
    run_dirs: list[str] = dataclasses.field(default_factory=list)
    manifest: dict[str, str] = dataclasses.field(default_factory=dict)
    differences: list[str] = dataclasses.field(default_factory=list)
    provenance: dict[str, Any] = dataclasses.field(default_factory=dict)


def artifact_manifest(run_dir: str | Path) -> dict[str, str]:
    """Map every artifact in ``run_dir`` to its sha256, sorted by relative path."""
    run_dir = Path(run_dir)
    manifest: dict[str, str] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir)
        if rel.parts[0] in BOOKKEEPING_DIRS or rel.name in BOOKKEEPING:
            continue
        manifest[rel.as_posix()] = file_sha256(path)
    return manifest


def _compare(manifests: list[dict[str, str]]) -> list[str]:
    """Describe how later runs diverge from the first one."""
    baseline = manifests[0]
    differences: list[str] = []
    for index, other in enumerate(manifests[1:], start=2):
        for rel in sorted(set(baseline) | set(other)):
            first, later = baseline.get(rel), other.get(rel)
            if first == later:
                continue
            if first is None:
                differences.append(f"{rel}: absent in run 1, present in run {index}")
            elif later is None:
                differences.append(f"{rel}: present in run 1, absent in run {index}")
            else:
                differences.append(f"{rel}: run 1 {first[:12]}… != run {index} {later[:12]}…")
    return differences


def reproduce(
    spec: Spec,
    times: int = 2,
    root: str | Path = ".",
    results_dir: str | Path = "results",
) -> ReproduceResult:
    """Run ``spec`` ``times`` times and compare the artifacts of every run.

    Raises :class:`ReproduceError` if a run fails or if the spec produces
    nothing comparable — a gate that compares zero artifacts would pass
    unconditionally, which is worse than having no gate at all.
    """
    if times < 2:
        raise ReproduceError(f"--times must be at least 2 to compare runs, got {times}")

    runner = Runner(root=root, results_dir=results_dir)
    manifests: list[dict[str, str]] = []
    run_dirs: list[str] = []
    for attempt in range(1, times + 1):
        result = runner.run(spec)
        write_reports(result)
        run_dirs.append(result.run_dir)
        if not result.success:
            failed = [
                f"{s.step_id}: {c.detail}" for s in result.steps for c in s.checks if not c.passed
            ]
            raise ReproduceError(
                f"run {attempt}/{times} of spec '{spec.name}' failed — "
                f"fix the spec before gating determinism. Failures: {failed}"
            )
        manifests.append(artifact_manifest(result.run_dir))

    if not any(manifests):
        raise ReproduceError(
            f"spec '{spec.name}' produced no comparable artifacts. A determinism "
            "gate over zero files always passes; have a step write its output "
            "into ${HARNESS_RESULTS_DIR} so there is something to compare."
        )

    differences = _compare(manifests)
    return ReproduceResult(
        spec_name=spec.name,
        times=times,
        reproducible=not differences,
        run_dirs=run_dirs,
        manifest=manifests[0],
        differences=differences,
        provenance=collect_provenance(root, seed=spec.seed),
    )


def write_reproduce_report(result: ReproduceResult, results_dir: str | Path) -> Path:
    """Write ``reproduce.json`` so CI can archive the comparison."""
    path = Path(results_dir) / "reproduce.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dataclasses.asdict(result), indent=2) + "\n",
        encoding="utf-8",
    )
    return path
