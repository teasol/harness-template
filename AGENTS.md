# AGENTS.md

Ground rules for working on **the `research-harness` package itself**. Read this
before making any change.

This repository is not a harness project. It does not run the harness on itself,
has no `.harness/`, no plans, no Planner and no task board — it is the source of
the tool that gives other repositories those things. Do not add them here: a
second copy of the configuration is exactly what drifted for three releases, and
`tests/test_config_sources.py` now fails if one reappears.

## What ships, and what does not

Everything a project receives lives in **`harness/templates/`**, and
`harness init` copies it out ([harness/init.py](harness/init.py)). That matters
more than it looks: a user installs with `pip install` or `uv add`, so they never
have this checkout — the role contracts, the agent configuration, the demo spec
and the `.gitignore` have to be inside the package or they do not exist for
them.

So when you change a contract or a config, change the file under
`harness/templates/`. There is no root copy any more.

Two files at the root are *not* copies and stay:

- `configs/demo.yaml` + `scripts/demo_step.py` — this package's own end-to-end
  smoke test, run by CI and `make verify`. The shipped twin differs on purpose:
  it points at `${HARNESS_DIR}/scripts/`, because in a scaffolded project the
  script lives under `.harness/`.
- `.gitignore` — this repository's own, like any repository's.

## Rules

1. **stdlib + PyYAML.** No runtime dependency beyond PyYAML. The harness runs
   inside other people's environments and must not fight their dependency
   resolution. Dev-only tools (pytest, ruff) go in `[project.optional-dependencies]`.
2. **Behaviour is proven by a test, not by a docstring.** Every fix lands with a
   test that fails without it. The suite is fast (~100s) and hermetic: no
   network, no API keys, no model calls. Where a coding agent would be, a shell
   command stands in.
3. **Tests must not write into the checkout.** Use `tmp_path`. A command that
   writes state — `plan new`, `task done`, `note`, `handoff` — takes a `--root`
   or `root=`; pass a temporary one. `HANDOFF.md` appearing at the repository
   root means a test leaked.
4. **Say why in the code, not only in the commit.** The comments and docstrings
   here carry the reasoning — what went wrong before, what the alternative was,
   why the default is what it is. That is deliberate: the next reader is usually
   a model with no memory of the incident. Keep it, and add to it.
5. **The CHANGELOG is part of the change.** Entries go under `[Unreleased]` in
   `Added` / `Changed` / `Fixed`, and they explain the *reason*, not just the
   diff. Releases are separate `chore(release):` commits that bump
   `pyproject.toml`, `harness/__init__.py` and the heading together.
6. **Printed commands go through [harness/invocation.py](harness/invocation.py).**
   Never hardcode `harness ...` or `python -m harness ...` in output a user
   reads: the prefix depends on how the harness was invoked (`uv run harness`,
   the console script, or `python -m`). `invocation.cmd()` and
   `invocation.steps()` resolve it.
7. **`harness/setup.py:HEADER` is the source of the `agents.yaml` header.** The
   checked-in file is its output. Editing the file by hand desynchronizes it
   from what `harness setup` writes into real projects; run `make sync-configs`.
8. **A documented placeholder must exist.** Adapter commands are resolved with
   `str.format`, so a name `render_command` does not pass raises `KeyError` when
   the harness spawns the agent. This shipped twice (`{experiment}`, then
   `{plan}`); the tests now render every documented placeholder.

## Before you push

```bash
make lint          # ruff check + format check
make test          # pytest
make verify        # the package's own end-to-end spec
```

CI runs lint + tests on every push (Python 3.10–3.12). The heavier
`Verification` workflow — determinism gate, plan lifecycle, handoff and
resume — runs on pull requests, on manual dispatch, and weekly. **It does not
run on a push to `main`**, which is how a broken step once survived two
releases; if you change something it exercises, run its steps locally.
