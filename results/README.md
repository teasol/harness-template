# results/

Run outputs land here. **Everything except this README is gitignored.**

Layout produced by the harness:

```
results/
└── runs/
    └── <spec-name>-<timestamp>/
        ├── report.json      # machine-readable result
        ├── report.md        # human-readable summary
        ├── logs/            # per-step command + stdout/stderr
        └── ...              # step artifacts ($HARNESS_RESULTS_DIR)
```

Inspect the latest run:

```bash
ls -t results/runs | head -1
cat "results/runs/$(ls -t results/runs | head -1)/report.md"
```
