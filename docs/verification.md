# Verification reference

The harness turns declarative YAML specs into executable, checkable pipelines.

## CLI

```bash
python -m harness verify --spec configs/demo.yaml [--root DIR] [--results-dir DIR]
python -m harness hash <file> [<file> ...]     # sha256 helper
```

Exit codes: `0` = all steps and checks passed, `1` = verification failed,
`2` = usage/spec error.

## Spec format

```yaml
name: my-spec          # required-ish (defaults to the file stem)
description: ...       # optional
seed: 42               # optional; sets deterministic env vars for all steps
steps:                 # ordered list; execution stops at the first failure
  - id: train          # unique, non-empty string
    run: <shell cmd>   # executed with sh in the repo root (or step cwd)
    cwd: scripts/      # optional, relative to the repo root
    timeout: 3600      # optional, seconds
    env:               # optional, extra env vars for this step only
      KEY: value
    checks:            # optional, evaluated after the step succeeds
      - type: file_exists
        path: ${HARNESS_RESULTS_DIR}/metrics.json
```

### Runner-provided environment variables

Every step (and every check `path` param, via `${VAR}` expansion) can use:

| Variable | Value |
| --- | --- |
| `HARNESS_RESULTS_DIR` | Per-run artifact directory (`<results-dir>/runs/<name>-<timestamp>/`) |
| `HARNESS_RUN_ID` | Spec name |

When `seed` is set, the runner also exports `PYTHONHASHSEED` and
`CUBLAS_WORKSPACE_CONFIG=:4096:8` to every step (see
[reproducibility.md](reproducibility.md)).

## Built-in checks

| Type | Params | Passes when |
| --- | --- | --- |
| `file_exists` | `path` | The file exists (relative to the repo root, `${VAR}` expanded) |
| `file_hash` | `path`, `sha256` | The file's sha256 matches the expected digest |
| `json_metric` | `path`, `metric`, and any of `min`, `max`, `equals` | The numeric metric (dotted paths supported, e.g. `metrics.accuracy`) is within bounds |
| `text_contains` | `path`, `contains` (string or list) | The file contains all substring(s) |

Checks raise `CheckError` on failure; the runner records the message in the
report. If a step's command fails (non-zero exit or timeout), its checks are
skipped and the step is marked failed.

## Run outputs

Each run writes to `<results-dir>/runs/<name>-<timestamp>/`:

- `report.json` — machine-readable full result
- `report.md` — human-readable summary table + failed checks
- `logs/NN-<step_id>.log` — command, exit code, stdout/stderr per step
- step artifacts (whatever the steps wrote, conventionally into `$HARNESS_RESULTS_DIR`)

## Adding a check type

1. Implement `check_<name>(root: Path, params: dict) -> str` in
   `harness/checks.py` — raise `CheckError` to fail, return a short detail
   string to pass.
2. Register it in `CHECK_REGISTRY`.
3. Add tests in `tests/test_checks.py`.
4. Document it in the table above.
