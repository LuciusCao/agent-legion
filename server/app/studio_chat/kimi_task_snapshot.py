"""Bounded Kimi V1 task snapshots and terminal output."""

from __future__ import annotations

import math
import os
import stat
from dataclasses import dataclass
from typing import Any

from server.app.studio_chat.task_metadata_files import read_json

TERMINAL = frozenset({"completed", "failed", "killed", "lost"})
ACTIVE = frozenset({"created", "starting", "running", "awaiting_approval"})


@dataclass(frozen=True)
class BackgroundTask:
    task_id: str
    kind: str
    status: str
    description: str
    started_at: float | None
    finished_at: float | None
    heartbeat_at: float | None
    output_changed_at: float | None
    summary: str

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL or self.status == "timed_out"


def timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if 0 < value < 253402300800 and math.isfinite(value) else None
    return None


def display_text(value: str, limit: int) -> str:
    """JSON permits lone surrogates that cannot be emitted as UTF-8."""
    return value[:limit].encode("utf-8", errors="replace").decode("utf-8")


def output_tail(parent: int, *, terminal: bool) -> tuple[float | None, str]:
    """Use the same pinned task directory as metadata; read at most 2048 bytes."""
    try:
        if not stat.S_ISREG(os.stat("output.log", dir_fd=parent, follow_symlinks=False).st_mode):
            return None, ""
        fd = os.open("output.log", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return None, ""
            if not terminal:
                return info.st_mtime, ""
            source.seek(max(0, info.st_size - 2048))
            return info.st_mtime, source.read(2048).decode("utf-8", errors="replace")[-600:]
    except OSError:
        return None, ""


def read_task(
    parent: int, task_id: str, session_id: str, *, strict: bool = False
) -> BackgroundTask | None:
    spec = read_json(parent, "spec.json", strict=strict)
    if strict and not {"version", "id", "session_id", "kind"} <= spec.keys():
        raise ValueError("task specification is incomplete")
    if (
        spec.get("version") != 1
        or spec.get("id") != task_id
        or spec.get("session_id") != session_id
        or spec.get("kind") not in ("agent", "bash")
        or spec.get("owner_role", "root") != "root"
    ):
        return None
    state = read_json(parent, "runtime.json", strict=strict)
    status = state.get("status")
    if not isinstance(status, str) or status not in ACTIVE | TERMINAL:
        if strict:
            raise ValueError("task runtime has no supported status")
        return None
    terminal = status in TERMINAL
    output_at, summary = output_tail(parent, terminal=terminal)
    reason = state.get("failure_reason")
    if isinstance(reason, str) and reason:
        summary = display_text(reason, 600)
    description = spec.get("description")
    return BackgroundTask(
        task_id,
        str(spec["kind"]),
        "timed_out" if terminal and state.get("timed_out") is True else status,
        display_text(description, 240) if isinstance(description, str) else task_id,
        timestamp(state.get("started_at")) or timestamp(spec.get("created_at")),
        timestamp(state.get("finished_at")),
        timestamp(state.get("heartbeat_at")),
        output_at,
        summary,
    )
