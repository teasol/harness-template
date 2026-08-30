# Integrations

Optional, tool-specific shims. **Nothing here is required.** The harness is
driven entirely by `python -m harness ...`, so any agent runtime — or a human
in a terminal — can do everything without installing any of this.

These exist only to save typing. Each is a few lines that call a harness
command and tell the session to follow its output. Copy the one you want into
wherever your tool keeps such things; skip the rest.

## Registering a Planner

The substance lives in the harness, not in a shim:

```bash
python -m harness planner brief <branch> --register <your-label>
```

That prints everything a session needs to act as that branch's Planner:
the role contract to read, the worktree and branch it owns, the plan's current
state, the module board, the exact commands to run, and how to hand back. A
session becomes the Planner by running it and following it.

So the entire shim, for any tool, is:

> Run `python -m harness planner brief <branch> --register <label>` in
> `<project path>` and follow the output. You are that branch's Planner
> until told otherwise.

### As a plain prompt

Say this to a fresh session — the agent runs the command itself, so there is
nothing to copy back and forth:

```
You are the Planner for the <branch> branch in <absolute project path>.

Run `python -m harness planner brief <branch> --register <your-label>`
and follow it exactly. Re-run it whenever you need current state.

You are the Main Worker as well as the Planner: implement modules yourself
when that is cheaper than briefing a Sub-Worker. Do not merge anything.
```

The briefing is a command rather than a pasted block on purpose: a paste is a
snapshot that goes stale the moment a Worker finishes, while a command returns
the board as it is now.

### As a file-based command

Most agent tools support some form of reusable prompt — a slash command, a
saved prompt, a skill, a rules file. They differ in name and location, so the
repository ships none of them by default: adding one would bind this template
to a single vendor, which is exactly what the harness avoids.

To make one, put the prompt above in whatever file your tool expects, with the
branch name as its argument. Two or three lines is the whole thing.

## Configuring Workers

Workers are invoked by `configs/agents.yaml`. The default (`manual`) writes a
briefing for a human to hand to a session, so the template works with no setup.

To automate, set `adapter: cli` and point `command` at your coding agent's
non-interactive mode. The requirements are only:

1. It reads the briefing from **stdin**.
2. It edits files under the working directory it is given.
3. It exits when finished.

If your tool can resume a session, set `resume_command` too: retries then keep
the worker's context instead of starting from nothing, which is usually both
cheaper and more likely to succeed.

The harness deliberately does not ship a command for any specific tool. Flags
change between versions and between vendors; a stale example that silently
does the wrong thing is worse than none. Check your tool's own documentation
for its non-interactive invocation, and put it in `configs/agents.yaml`.
