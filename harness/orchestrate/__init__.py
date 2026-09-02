"""The two tiers: a Planner that is also the Main Worker, and Sub-Workers.

A plan is one piece of work on its own git branch in its own worktree. It
decomposes into modules; each becomes a task with a contract and machine-checked
acceptance. Every module belongs to the Planner unless it says
``executor: sub``, in which case a Sub-Worker is spawned for that one module and
hands back. This layer decides *who does what and in what order*; whether the
result is acceptable is `harness.verify`'s answer, never this layer's.

Deliberately empty of re-exports — see `harness.verify` for why.
"""
