"""Read-only, descriptor-anchored Kimi V1 task compatibility boundary."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Collection
from pathlib import Path

from server.app.studio_chat.kimi_task_snapshot import BackgroundTask, read_task
from server.app.studio_chat.task_metadata_files import DIRECTORY_FLAGS, directory

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")


def task_root(cwd: str, session_id: str) -> Path | None:
    if not _ID.fullmatch(session_id):
        return None
    share = Path(os.environ.get("KIMI_SHARE_DIR", str(Path.home() / ".kimi")))
    if not share.is_absolute():
        share = Path(cwd) / share
    digest = hashlib.md5(str(Path(cwd).resolve()).encode(), usedforsecurity=False).hexdigest()
    return share.resolve() / "sessions" / digest / session_id / "tasks"


def task_snapshots(
    root: Path, session_id: str, *, ignored: Collection[str] = ()
) -> dict[str, BackgroundTask]:
    """Keep spec, runtime and output on one pinned task directory."""
    result: dict[str, BackgroundTask] = {}
    try:
        with directory(root) as root_fd:
            for name in os.listdir(root_fd):
                if name in ignored or not _ID.fullmatch(name):
                    continue
                try:
                    task_fd = os.open(name, DIRECTORY_FLAGS, dir_fd=root_fd)
                    try:
                        task = read_task(task_fd, name, session_id)
                    finally:
                        os.close(task_fd)
                    if task is not None:
                        result[name] = task
                except (OSError, ValueError):
                    continue
    except (OSError, ValueError):
        return result
    return result


def completed_tasks(
    root: Path, session_id: str, *, ignored: Collection[str] = ()
) -> dict[str, str]:
    """Only root agent completions qualify for automatic followups."""
    return {
        key: task.status
        for key, task in task_snapshots(root, session_id, ignored=ignored).items()
        if task.kind == "agent" and task.terminal
    }
