"""Loading and validation of verification specs (YAML).

A spec describes an ordered list of steps. Each step runs a shell command and
is followed by zero or more checks. See ``docs/verification.md`` for the full
reference.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml


class SpecError(ValueError):
    """Raised when a spec cannot be parsed or is semantically invalid."""


@dataclasses.dataclass
class Check:
    """A single verification check attached to a step."""

    type: str
    params: dict[str, Any]

    @classmethod
    def from_dict(cls, data: Any) -> Check:
        if not isinstance(data, dict):
            raise SpecError(f"check must be a mapping, got: {data!r}")
        if "type" not in data:
            raise SpecError(f"check is missing the 'type' key: {data!r}")
        check_type = data["type"]
        if not isinstance(check_type, str) or not check_type:
            raise SpecError(f"check 'type' must be a non-empty string: {data!r}")
        params = {k: v for k, v in data.items() if k != "type"}
        return cls(type=check_type, params=params)


@dataclasses.dataclass
class Step:
    """A single command execution plus its post-run checks."""

    id: str
    run: str
    cwd: str | None = None
    timeout: int | float | None = None
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    checks: list[Check] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> Step:
        if not isinstance(data, dict):
            raise SpecError(f"step must be a mapping, got: {data!r}")
        if "id" not in data or "run" not in data:
            raise SpecError(f"step requires 'id' and 'run' keys: {data!r}")
        step_id = data["id"]
        if not isinstance(step_id, str) or not step_id:
            raise SpecError(f"step 'id' must be a non-empty string: {data!r}")
        if not isinstance(data["run"], str) or not data["run"]:
            raise SpecError(f"step '{step_id}': 'run' must be a non-empty string")

        checks_data = data.get("checks", [])
        if not isinstance(checks_data, list):
            raise SpecError(f"step '{step_id}': 'checks' must be a list")
        checks = [Check.from_dict(c) for c in checks_data]

        env = data.get("env", {})
        if not isinstance(env, dict):
            raise SpecError(f"step '{step_id}': 'env' must be a mapping")

        timeout = data.get("timeout")
        if timeout is not None and not isinstance(timeout, (int, float)):
            raise SpecError(f"step '{step_id}': 'timeout' must be a number")

        return cls(
            id=step_id,
            run=data["run"],
            cwd=data.get("cwd"),
            timeout=timeout,
            env={str(k): str(v) for k, v in env.items()},
            checks=checks,
        )


@dataclasses.dataclass
class Spec:
    """A full verification spec: metadata plus ordered steps."""

    name: str
    description: str = ""
    seed: int | None = None
    #: Opt in to env vars that force deterministic GPU math. These change
    #: kernel selection and therefore the numbers, so declaring a seed does not
    #: imply them — see :func:`harness.reproducibility.math_env`.
    deterministic_math: bool = False
    steps: list[Step] = dataclasses.field(default_factory=list)
    source: Path | None = None


def load_spec(path: str | Path) -> Spec:
    """Load and validate a spec from a YAML file."""
    path = Path(path)
    if not path.is_file():
        raise SpecError(f"spec file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SpecError(f"invalid YAML in {path}: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SpecError(f"spec root must be a mapping, got: {type(raw).__name__}")

    name = raw.get("name", path.stem)
    if not isinstance(name, str) or not name:
        raise SpecError("'name' must be a non-empty string")

    seed = raw.get("seed")
    if seed is not None and not isinstance(seed, int):
        raise SpecError("'seed' must be an integer")

    deterministic_math = raw.get("deterministic_math", False)
    if not isinstance(deterministic_math, bool):
        raise SpecError("'deterministic_math' must be a boolean")

    steps_data = raw.get("steps", [])
    if not isinstance(steps_data, list):
        raise SpecError("'steps' must be a list")
    steps = [Step.from_dict(s) for s in steps_data]

    ids = [s.id for s in steps]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SpecError(f"duplicate step ids: {duplicates}")

    return Spec(
        name=name,
        description=str(raw.get("description", "")),
        seed=seed,
        deterministic_math=deterministic_math,
        steps=steps,
        source=path,
    )
