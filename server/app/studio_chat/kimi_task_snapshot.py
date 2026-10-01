"""Bounded, read-only Kimi V1 metadata and terminal output parsing."""

from __future__ import annotations

import json
import math
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TERMINAL = frozenset({"completed", "failed", "killed", "lost"})
ACTIVE = frozenset({"created", "starting", "running", "awaiting_approval"})


@contextmanager
def _open(path: Path, root: Path):
    relative = path.relative_to(root)
    if len(relative.parts) != 2 or ".." in relative.parts:
        raise ValueError("Invalid task file path")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        task_fd = os.open(
            relative.parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
        try:
            fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=task_fd)
            with os.fdopen(fd, "rb") as source:
                yield source
        finally:
            os.close(task_fd)
    finally:
        os.close(root_fd)


def read_state(path: Path, root: Path) -> dict[str, Any]:
    if path.resolve() != path or not path.is_relative_to(root):
        return {}
    if not stat.S_ISREG(path.lstat().st_mode):
        return {}
    with _open(path, root) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return {}
        data = source.read(65537)
    if len(data) > 65536:
        return {}
    value = json.loads(data)
    return value if isinstance(value, dict) else {}


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


def output_tail(path: Path, root: Path, *, terminal: bool) -> tuple[float | None, str]:
    """Stat running output; only terminal receipts read a bounded tail."""
    if path.resolve() != path or not path.is_relative_to(root):
        return None, ""
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None, ""
        with _open(path, root) as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return None, ""
            if not terminal:
                return info.st_mtime, ""
            source.seek(max(0, info.st_size - 2048))
            return info.st_mtime, source.read(2048).decode("utf-8", errors="replace")[-600:]
    except OSError:
        return None, ""
