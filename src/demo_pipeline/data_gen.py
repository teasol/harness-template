"""Module 1 — generate a deterministic demo dataset.

Deliverable of the ``data-gen`` task in ``plans/demo-pipeline.yaml``.
The Worker who owns that task implements this module against the task's
contract; the acceptance steps in the task file verify it.
"""

from __future__ import annotations

import argparse
import csv
import random
from collections.abc import Sequence
from pathlib import Path


def generate_rows(seed: int, n: int = 100) -> list[tuple[float, float]]:
    """Return ``n`` deterministic (x, y) rows: y ≈ 2x + 1 + noise."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        x = rng.uniform(0, 100)
        y = 2.0 * x + 1.0 + rng.gauss(0, 5.0)
        rows.append((round(x, 6), round(y, 6)))
    return rows


def write_csv(rows: Sequence[tuple[float, float]], path: str | Path) -> Path:
    """Write rows to ``path`` as a CSV with header ``x,y``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["x", "y"])
        writer.writerows(rows)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Output CSV path")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n", type=int, default=100, help="Number of rows")
    args = parser.parse_args(argv)
    out = write_csv(generate_rows(args.seed, args.n), args.out)
    print(f"wrote {args.n} rows to {out} (seed={args.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
