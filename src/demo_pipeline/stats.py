"""Module 2 — compute summary statistics from a demo dataset CSV.

Deliverable of the ``stats`` task in ``plans/demo-pipeline.yaml``.
Consumes the ``data-gen`` module's output (its contract input) without
knowing anything else about the pipeline.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections.abc import Sequence
from pathlib import Path


def read_rows(path: str | Path) -> list[tuple[float, float]]:
    """Read a CSV with header ``x,y`` into a list of (x, y) tuples."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != ["x", "y"]:
            raise ValueError(f"unexpected header in {path}: {header!r}")
        return [(float(row[0]), float(row[1])) for row in reader]


def compute_stats(rows: Sequence[tuple[float, float]]) -> dict[str, float | int]:
    """Return summary statistics, including a least-squares line fit."""
    xs = [x for x, _ in rows]
    ys = [y for _, y in rows]
    n = len(rows)
    if n < 2:
        raise ValueError("need at least 2 rows")
    x_mean, y_mean = statistics.fmean(xs), statistics.fmean(ys)
    var_x = statistics.fmean([(x - x_mean) ** 2 for x in xs])
    cov_xy = statistics.fmean([(x - x_mean) * (y - y_mean) for x, y in rows])
    slope = cov_xy / var_x
    intercept = y_mean - slope * x_mean
    return {
        "n": n,
        "x_mean": round(x_mean, 6),
        "y_mean": round(y_mean, 6),
        "x_std": round(statistics.pstdev(xs), 6),
        "y_std": round(statistics.pstdev(ys), 6),
        "slope": round(slope, 6),
        "intercept": round(intercept, 6),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Input CSV path (x,y header)")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args(argv)
    stats = compute_stats(read_rows(args.input))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    print(f"wrote stats to {out} (n={stats['n']}, slope={stats['slope']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
