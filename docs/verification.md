# Verification reference

The harness turns declarative YAML specs into executable, checkable pipelines.

## CLI

```bash
python -m harness verify --spec configs/demo.yaml [--root DIR] [--results-dir DIR]
python -m harness reproduce --spec configs/demo.yaml [--times N]  # determinism gate
python -m harness hash <file> [<file> ...]                        # sha256 helper
```

Exit codes: `0` = all steps and checks passed, `1` = verification failed,
`2` = usage/spec error.

### `reproduce` — the determinism gate

`reproduce` runs a spec `--times` (default 2) and diffs a manifest of every
artifact each run wrote. Harness bookkeeping (`report.json`, `report.md`,
`logs/`) is excluded, since it records timestamps and durations that differ by
construction; what remains is the run's research output.

- `0` — every artifact is byte-identical across runs
- `1` — at least one artifact diverged; the differing paths and digests are
  printed and stored in `reproduce.json`
- `2` — the spec failed, or produced **no** artifacts to compare

That last case is deliberate: a determinism gate over zero files passes
unconditionally and is worse than no gate, so the harness refuses it rather
than reporting a green tick. Write step outputs into `${HARNESS_RESULTS_DIR}`
and they are gated automatically.

## Spec format

```yaml
name: my-spec          # required-ish (defaults to the file stem)
description: ...       # optional
seed: 42               # optional; exports PYTHONHASHSEED to all steps
deterministic_math: false  # optional; opt in to CUBLAS_WORKSPACE_CONFIG — CHANGES RESULTS
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
| `HARNESS_PYTHON` | Absolute path to the interpreter running the harness |
| `HARNESS_SEED` | The spec's `seed` (unset when the spec declares none) |

**Always call the interpreter as `${HARNESS_PYTHON}`, never as bare `python`** —
Debian/Ubuntu and many CI images ship only `python3`, so a hardcoded `python`
makes a spec fail on a fresh checkout. Likewise, spell seeds
`--seed ${HARNESS_SEED}` so the spec's `seed:` stays the single source of
truth instead of being duplicated in a command string.

When `seed` is set, the runner exports `PYTHONHASHSEED` to every step. It does
**not** set `CUBLAS_WORKSPACE_CONFIG` — that changes results, so it is opt-in
via `deterministic_math: true` (see [reproducibility.md](reproducibility.md)).
Whatever the harness injects is recorded in the run's provenance and printed in
the report, so it is never a hidden variable.

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

**`json_metric` compares numbers, and a JSON boolean is not one.** Emit a
pass/fail flag as `1` / `0`, not `true` / `false`:

```json
{ "within_tolerance": 1 }
```

`true` is rejected rather than coerced, because `equals: 1` and `equals: true`
would otherwise be the same assertion — and a flag that compares equal to a
measurement is a bug waiting for a bad day.

## Run outputs

Each run writes to `<results-dir>/runs/<name>-<timestamp>/`:

- `report.json` — machine-readable full result (including `provenance`)
- `report.md` — human-readable summary table, provenance block, failed checks
- `logs/NN-<step_id>.log` — command, exit code, stdout/stderr per step
- step artifacts (whatever the steps wrote, conventionally into `$HARNESS_RESULTS_DIR`)

### Provenance

Every report records what produced the run, so a result found months later can
be traced back to code: `git_commit`, `git_branch`, `git_dirty`, the Python
version and interpreter path, the platform string, the harness version, and the
declared `seed`. Each field is best-effort — outside a git checkout the git
fields are `null` rather than an error.

A `git_dirty: true` report was produced from an uncommitted worktree and is
**not** reproducible from the commit alone; treat it as a draft result.

## Adding a check type

1. Implement `check_<name>(root: Path, params: dict) -> str` in
   `harness/checks.py` — raise `CheckError` to fail, return a short detail
   string to pass.
2. Register it in `CHECK_REGISTRY`.
3. Add tests in `tests/test_checks.py`.
4. Document it in the table above.
