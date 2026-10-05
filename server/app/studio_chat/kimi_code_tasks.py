"""Read-only Kimi Code (0.43+) background-task storage layout (#972).

Kimi CLI V1 keeps one directory per task (``spec.json`` / ``runtime.json`` /
``output.log``, kimi_task_store.py). Kimi Code keeps the main agent's tasks
beside its wire journal (homes probed like kimi_wire.kimi_code_homes):
``sessions/<workspace-id>/<acp-session-id>/agents/main/tasks/<taskId>.json`` —
one camelCase info document per task, timestamps in milliseconds, kind
``agent`` or ``process`` — and the task output at ``tasks/<taskId>/output.log``.
A subagent writes its output only when it settles, so its progress signal is
its own journal ``agents/<agentId>/wire.jsonl``.

Every read is a descriptor walk through ``fs_safety`` with the shared
task-metadata soft-fail policy (task_metadata_files.read_json).
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Collection
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from server.app.fs_safety import DIRECTORY_FLAGS
from server.app.fs_safety import open_dir_nofollow as directory
from server.app.studio_chat.kimi_task_snapshot import (
    TERMINAL,
    BackgroundTask,
    display_text,
    output_tail,
    timestamp,
)
from server.app.studio_chat.kimi_wire import kimi_code_homes, session_dirs
from server.app.studio_chat.task_metadata_files import read_json

CODE_TASK_PARTS = ("agents", "main", "tasks")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_KINDS = {"agent": "agent", "process": "bash"}
_STATUSES = TERMINAL | {"running", "timed_out"}


def kimi_code_task_root(cwd: str, session_id: str) -> Path | None:
    """The main agent's task directory, once the Kimi Code session directory
    exists (location only: I/O beneath it walks ``fs_safety`` descriptors)."""
    for session_dir in session_dirs(kimi_code_homes(cwd), session_id):
        if session_dir.is_dir():
            return session_dir.joinpath(*CODE_TASK_PARTS)
    return None


def is_code_task_root(root: Path) -> bool:
    # A V1 root ends ``<md5>/<session>/tasks``: an md5 is never ``agents``.
    return root.parts[-3:] == CODE_TASK_PARTS


def _millis(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return timestamp(value / 1000)
    return None


def _entry_mtime(parent: int, directory_name: str, file_name: str) -> float | None:
    try:
        fd = os.open(directory_name, DIRECTORY_FLAGS, dir_fd=parent)
    except OSError:
        return None
    try:
        info = os.stat(file_name, dir_fd=fd, follow_symlinks=False)
    except OSError:
        return None
    finally:
        os.close(fd)
    return info.st_mtime if stat.S_ISREG(info.st_mode) else None


def _output(root_fd: int, task_id: str, *, terminal: bool) -> tuple[float | None, str]:
    try:
        task_fd = os.open(task_id, DIRECTORY_FLAGS, dir_fd=root_fd)
    except OSError:
        return None, ""  # No output yet.
    try:
        return output_tail(task_fd, terminal=terminal)
    finally:
        os.close(task_fd)


def read_code_task(
    root_fd: int, agents_fd: int | None, task_id: str, *, strict: bool = False
) -> BackgroundTask | None:
    info = read_json(root_fd, f"{task_id}.json", strict=strict)
    kind = _KINDS.get(str(info.get("kind")))
    # Foreground (non-detached) work is a plain tool call, not a task.
    if info.get("taskId") != task_id or kind is None or info.get("detached") is False:
        return None
    status = info.get("status")
    if not isinstance(status, str) or status not in _STATUSES:
        if strict:
            raise ValueError("task info has no supported status")
        return None
    terminal = status != "running"
    output_at, summary = _output(root_fd, task_id, terminal=terminal)
    agent_id = str(info.get("agentId") or "")
    if kind == "agent" and agents_fd is not None and _ID.fullmatch(agent_id) and agent_id != "main":
        progress = _entry_mtime(agents_fd, agent_id, "wire.jsonl")
        output_at = max(filter(None, (output_at, progress)), default=None)
    reason = info.get("stopReason")
    if terminal and isinstance(reason, str) and reason:
        summary = display_text(reason, 600)
    description = info.get("description")
    return BackgroundTask(
        task_id,
        kind,
        status,
        display_text(description, 240) if isinstance(description, str) and description else task_id,
        _millis(info.get("startedAt")),
        _millis(info.get("endedAt")),
        None,
        output_at,
        summary,
    )


def code_task_snapshots(
    root: Path, *, ignored: Collection[str] = (), strict: bool = False
) -> dict[str, BackgroundTask]:
    """Snapshot the main agent's tasks; a missing task directory is empty
    (Kimi Code creates it with the first background task)."""
    result: dict[str, BackgroundTask] = {}
    with ExitStack() as stack:
        try:
            root_fd = stack.enter_context(directory(root))
        except FileNotFoundError:
            return result
        except (OSError, ValueError):
            if strict:
                raise
            return result
        try:
            agents_fd: int | None = stack.enter_context(directory(root.parent.parent))
        except (OSError, ValueError):
            agents_fd = None
        try:
            names = sorted(os.listdir(root_fd))
        except OSError:
            if strict:
                raise
            return result
        for name in names:
            task_id = name.removesuffix(".json")
            if task_id == name or task_id in ignored or not _ID.fullmatch(task_id):
                continue
            try:
                task = read_code_task(root_fd, agents_fd, task_id, strict=strict)
            except (OSError, ValueError):
                if strict:
                    raise
                continue
            if task is not None:
                result[task_id] = task
    return result
