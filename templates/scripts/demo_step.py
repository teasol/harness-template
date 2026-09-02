"""Deterministic example command for a project's first checklist item.

Produces a small JSON artifact from a seeded RNG so that runs are stable —
a checklist item's verdict is its exit code, and this one always exits zero
when it can write its output.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--value", type=int, default=None, help="Override the random value (exact determinism)"
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    value = args.value if args.value is not None else rng.randint(0, 100)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "value": value,
        "seed": args.seed,
        "generator": "scripts/demo_step.py",
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} (value={value})")


if __name__ == "__main__":
    main()
