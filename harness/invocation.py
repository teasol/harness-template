"""How the harness was invoked — so the commands it prints can be pasted.

Every command the harness prints is meant to be copied into a shell, and which
form works depends on how *this* process was started:

* installed as a console script (``pip install``, ``uv tool install``) → ``harness``
* run from a checkout → ``python -m harness``
* added to a project with ``uv add`` → ``uv run harness``, because neither
  ``harness`` nor ``python -m harness`` is on the path outside the venv

The third case is the one that used to be wrong: a user who ran
``uv run harness init`` was told to run ``harness create -n <planner-name>``
next, which is not a command they have. So the prefix is read from the
environment rather than hardcoded, and every printed next step goes through
:func:`cmd` or :func:`steps`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_PREFIX = "harness"
MODULE_PREFIX = "python -m harness"
UV_PREFIX = "uv run harness"


def _under_uv_run(env: os._Environ | dict) -> bool:
    """True when ``uv run`` started this process.

    ``uv run`` exports ``UV_RUN_RECURSION_DEPTH`` (and ``UV``, the path to the
    uv binary). ``uv tool install`` does not: that puts ``harness`` on the PATH,
    where the plain script name is the right thing to print.
    """
    if env.get("UV_RUN_RECURSION_DEPTH"):
        return True
    return bool(env.get("UV")) and bool(env.get("VIRTUAL_ENV"))


def _invoked_as_module(argv: list[str]) -> bool:
    """True for ``python -m harness``, where argv[0] is the package's __main__."""
    if not argv or not argv[0]:
        return True
    return Path(argv[0]).name == "__main__.py"


def command_prefix(env: dict | None = None, argv: list[str] | None = None) -> str:
    """The prefix a user of this session has to type to reach the harness."""
    env = os.environ if env is None else env
    argv = sys.argv if argv is None else argv
    if _under_uv_run(env):
        return UV_PREFIX
    if _invoked_as_module(argv):
        return MODULE_PREFIX
    return SCRIPT_PREFIX


def cmd(rest: str = "") -> str:
    """One runnable command: the active prefix plus ``rest``."""
    prefix = command_prefix()
    return f"{prefix} {rest}".rstrip()


def steps(pairs: list[tuple[str, str]]) -> list[str]:
    """Render ``(command, why)`` pairs, comments aligned.

    Alignment is computed rather than typed, because the prefix changes width:
    hand-padded comments line up under ``harness`` and not under
    ``uv run harness``.
    """
    rendered = [(cmd(command), why) for command, why in pairs]
    width = max((len(command) for command, why in rendered if why), default=0)
    return [f"{command:<{width}}   # {why}" if why else command for command, why in rendered]
