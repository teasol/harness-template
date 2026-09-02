"""What survives the end of a session.

The harness was built as if one Planner session lasts a project. It does not:
the context window fills, the laptop closes, tomorrow is a different machine and
sometimes a different tool. This layer is what a session that was not here reads
instead of being told — the handoff document, the Planner registry it draws on,
the project's own conventions, and how the harness arrived in a codebase that
already existed.

Deliberately empty of re-exports — see `harness.verify` for why.
"""
