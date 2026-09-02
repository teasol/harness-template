"""Where the harness is right now, readable from another terminal.

Long operations here are opaque by construction: both the step runner and the
Worker adapter call ``subprocess.run(capture_output=True)``, which buffers
everything until the child exits. A Worker attempt may run to a thirty-minute
cap, and for those thirty minutes there is no output, no elapsed time, and no
way to tell a working agent from a wedged one. That happened: the single most
expensive attempt of a real run was also the least observable moment in it.

A plan is a serial thing — module 2 of 3, attempt 2 of 6, step 1 of 4 — so the
position is always knowable. This module writes it to one small file and keeps
a timestamp ticking while the work runs, so a second terminal can answer both
questions that matter during a long wait:

- **Where am I?** — the position, and when this leg started.
- **Is it still alive?** — how long ago the timestamp moved. A heartbeat that
  stopped ticking means the process died without cleaning up, which is
  otherwise indistinguishable from slow work.

Writing is best-effort. A read-only or missing results directory must never
take down the run it was only supposed to describe.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILENAME = "heartbeat.json"

#: How often the ticker refreshes ``updated_at`` while work is in flight.
TICK_SECONDS = 5.0

#: A heartbeat older than this many ticks is treated as abandoned rather than
#: slow — generous, because a loaded machine can delay a thread.
STALE_AFTER_TICKS = 6


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def heartbeat_path(results_dir: str | Path) -> Path:
    return Path(results_dir) / FILENAME


@dataclasses.dataclass
class Heartbeat:
    """One in-flight leg of work, as last reported."""

    activity: str = ""
    label: str = ""
    position: str = ""
    started_at: str = ""
    updated_at: str = ""
    timeout_s: float | None = None
    pid: int = 0
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def started(self) -> datetime | None:
        return _parse(self.started_at)

    @property
    def updated(self) -> datetime | None:
        return _parse(self.updated_at)

    @property
    def elapsed_s(self) -> float:
        started = self.started
        return (_now() - started).total_seconds() if started else 0.0

    @property
    def silent_s(self) -> float:
        """Seconds since the ticker last moved. Large means nobody is home."""
        updated = self.updated
        return (_now() - updated).total_seconds() if updated else float("inf")

    @property
    def is_stale(self) -> bool:
        return self.silent_s > TICK_SECONDS * STALE_AFTER_TICKS

    @property
    def remaining_s(self) -> float | None:
        if self.timeout_s is None:
            return None
        return max(0.0, self.timeout_s - self.elapsed_s)

    def describe(self) -> str:
        """One line a human can read while waiting."""
        where = f"{self.activity} {self.label}".strip()
        if self.position:
            where += f" ({self.position})"
        parts = [where, f"running {human_duration(self.elapsed_s)}"]
        remaining = self.remaining_s
        if remaining is not None:
            parts.append(f"{human_duration(remaining)} before the cap")
        if self.is_stale:
            parts.append(
                f"NO HEARTBEAT for {human_duration(self.silent_s)} — the process "
                "probably died without cleaning up"
            )
        return " · ".join(parts)


def _parse(value: str) -> datetime | None:
    if not value:
        return None
    with contextlib.suppress(ValueError):
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return None


def human_duration(seconds: float) -> str:
    """Durations a person reads at a glance: 45s, 12m30s, 2h05m."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def read(results_dir: str | Path) -> Heartbeat | None:
    """The current heartbeat, or None when nothing is running (or it is unreadable)."""
    path = heartbeat_path(results_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("activity"):
        return None
    return Heartbeat(
        activity=str(data.get("activity", "")),
        label=str(data.get("label", "")),
        position=str(data.get("position", "")),
        started_at=str(data.get("started_at", "")),
        updated_at=str(data.get("updated_at", "")),
        timeout_s=data.get("timeout_s"),
        pid=int(data.get("pid", 0) or 0),
        detail=data.get("detail") or {},
    )


def clear(results_dir: str | Path) -> None:
    """Remove the heartbeat — nothing is running any more."""
    with contextlib.suppress(OSError):
        heartbeat_path(results_dir).unlink(missing_ok=True)


class Beat:
    """Context manager that publishes one leg of work and keeps it ticking.

    Used around any call that blocks for an unbounded time::

        with Beat(results_dir, "step", step.id, "2/4", timeout_s=step.timeout):
            subprocess.run(...)

    The ticker runs on a daemon thread, so it can never hold the process open,
    and every write is atomic so a reader never sees half a file.
    """

    def __init__(
        self,
        results_dir: str | Path,
        activity: str,
        label: str,
        position: str = "",
        timeout_s: float | None = None,
        detail: dict[str, Any] | None = None,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.path = heartbeat_path(results_dir)
        self.activity = activity
        self.label = label
        self.position = position
        self.timeout_s = timeout_s
        self.detail = detail or {}
        self.tick_seconds = tick_seconds
        self.started = _now()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> Beat:
        self._write()
        self._thread = threading.Thread(target=self._tick, name="harness-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.tick_seconds)
        clear(self.path.parent)

    # -- internals ---------------------------------------------------------

    def _tick(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            self._write()

    def _write(self) -> None:
        payload = {
            "activity": self.activity,
            "label": self.label,
            "position": self.position,
            "started_at": _iso(self.started),
            "updated_at": _iso(_now()),
            "timeout_s": self.timeout_s,
            "pid": os.getpid(),
            "detail": self.detail,
        }
        # Best-effort by design: a heartbeat is a convenience for a watcher, and
        # failing to describe the work must never fail the work.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass
