# Reproducibility policy

Verification is only meaningful if runs are reproducible. This template
enforces determinism at three levels, on top of a provenance record that says
what produced each run.

## 0. Provenance

Every `report.json` carries a `provenance` block (git commit + dirty flag,
branch, Python version and interpreter, platform, harness version, seed) and
`report.md` renders it. Without it "the numbers came out different" is
unanswerable; with it, the first question — *was this even the same code?* —
is answered by the artifact itself. Reports produced from a dirty worktree are
flagged, because they cannot be reconstructed from a commit.

## 1. Seeds

- Declare a `seed` in the spec; the runner exports `PYTHONHASHSEED` to every
  step. Seeding is safe: nothing here changes which kernels a numerical
  library selects, so numbers stay comparable to runs made outside the harness.
- **Deterministic GPU math is separate, and opt-in.** Setting
  `deterministic_math: true` exports `CUBLAS_WORKSPACE_CONFIG=:4096:8`, which
  constrains cuBLAS algorithm selection and therefore **changes your numbers**.
  A measurement taken with it set is not directly comparable to one taken
  without it — the difference is a small systematic shift, not symmetric noise,
  which is exactly the kind of thing that silently eats a reproduction
  tolerance. Turn it on when you are chasing bit-level determinism; leave it
  off when you are reproducing a historical measurement that was taken without
  it. Either way the choice is recorded in provenance and shown in the report.
- In-process code should call `harness.reproducibility.set_all_seeds(seed)`,
  which seeds stdlib `random`, `numpy`, and `torch` (whichever are importable).
- Never use unseeded RNGs in committed code paths. Data loading shuffles,
  dropout, and initialization must all flow from the declared seed.

```python
from harness.reproducibility import set_all_seeds

set_all_seeds(config.experiment.seed)
```

## 2. Hash stability

`harness reproduce` runs a spec repeatedly and compares a hash manifest of
**every** artifact it produced — not one hand-picked file:

```bash
python -m harness reproduce --spec configs/exp.yaml --times 2
```

It exits non-zero on any divergence, and refuses to pass a spec that produced
nothing to compare. `make reproduce` wraps it and the CI verification workflow
runs it on every PR, so nondeterminism fails the PR rather than being noticed
months later. (`harness hash <file>` remains available for one-off digests.)

Notes:

- Mersenne Twister (`random.Random(seed)`) and numpy's legacy RNG are stable
  across platforms; prefer them for hash-gated artifacts.
- `torch` results may differ across GPU models/driver versions — gate hashes
  per-hardware, or assert metric *bounds* (`json_metric` with `min`/`max`)
  instead of exact hashes.
- For full torch determinism see
  [`torch.use_deterministic_algorithms(True)`](https://pytorch.org/docs/stable/generated/torch.use_deterministic_algorithms.html)
  — the harness already sets `CUBLAS_WORKSPACE_CONFIG` when a seed is declared.

## 3. Data & environment pinning

- `data/` is gitignored: document data provenance (source, version, download
  date) in `data/README.md`.
- Pin environments: `requirements`/`pyproject` + `environment.yml`, and record
  the exact environment in long-lived run reports (extend the report if
  needed).
- Keep heavy artifacts (checkpoints, predictions) out of git; reference them by
  path + hash in reports.

## Known nondeterminism sources to watch

| Source | Mitigation |
| --- | --- |
| Unseeded RNG | `set_all_seeds`, spec `seed` |
| Hash randomization | `PYTHONHASHSEED` (set by runner) |
| cuBLAS nondeterminism | `CUBLAS_WORKSPACE_CONFIG` (set by runner) |
| Parallel data loading | Fixed `num_workers`, `worker_init_fn` seeded |
| Floating-point reduction order | Fix batch sizes / thread counts; compare with tolerance |
