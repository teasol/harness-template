# Reproducibility policy

Verification is only meaningful if runs are reproducible. This template
enforces determinism at three levels.

## 1. Seeds

- Declare a `seed` in the spec; the runner exports `PYTHONHASHSEED` and
  `CUBLAS_WORKSPACE_CONFIG=:4096:8` to every step.
- In-process code should call `harness.reproducibility.set_all_seeds(seed)`,
  which seeds stdlib `random`, `numpy`, and `torch` (whichever are importable).
- Never use unseeded RNGs in committed code paths. Data loading shuffles,
  dropout, and initialization must all flow from the declared seed.

```python
from harness.reproducibility import set_all_seeds

set_all_seeds(config.experiment.seed)
```

## 2. Hash stability

`harness hash <file>` prints a file's sha256. The CI verification workflow runs
the demo spec **twice** and compares the artifact hashes — any divergence fails
the PR. Apply the same pattern to your own artifacts:

```bash
python -m harness verify --spec configs/exp.yaml --results-dir results/a
python -m harness verify --spec configs/exp.yaml --results-dir results/b
diff <(python -m harness hash results/a/runs/*/output.json) \
     <(python -m harness hash results/b/runs/*/output.json)
```

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
