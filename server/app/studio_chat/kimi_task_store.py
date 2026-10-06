"""Read-only, descriptor-anchored Kimi task compatibility boundary.

Kimi CLI V1 layout here; a Kimi Code root (kimi_code_tasks.py, #972) is
dispatched by its shape so every caller reads either layout.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Collection
from pathlib import Path

from server.app.fs_safety import DIRECTORY_FLAGS
from server.app.fs_safety import open_dir_nofollow as directory
from server.app.studio_chat.kimi_code_tasks import code_task_snapshots, is_code_task_root
from server.app.studio_chat.kimi_task_snapshot import BackgroundTask, read_task

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
    root: Path, session_id: str, *, ignored: Collection[str] = (), strict: bool = False
) -> dict[str, BackgroundTask]:
    """Keep spec, runtime and output on one pinned task directory."""
    if is_code_task_root(root):  # Kimi Code layout (#972)
        return code_task_snapshots(root, ignored=ignored, strict=strict)
    result: dict[str, BackgroundTask] = {}
    try:
        with directory(root) as root_fd:
            for name in os.listdir(root_fd):
                if name in ignored or not _ID.fullmatch(name):
                    continue
                try:
                    task_fd = os.open(name, DIRECTORY_FLAGS, dir_fd=root_fd)
                    try:
                        task = read_task(task_fd, name, session_id, strict=strict)
                        if strict and not os.path.samestat(
                            os.fstat(task_fd), os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                        ):
                            raise OSError("task directory changed during baseline scan")
                    finally:
                        os.close(task_fd)
                    if task is not None:
                        result[name] = task
                except (OSError, ValueError):
                    if strict:
                        raise
                    continue
            if strict:
                with directory(root) as current_fd:
                    if not os.path.samestat(os.fstat(root_fd), os.fstat(current_fd)):
                        raise OSError("task root changed during baseline scan")
    except (OSError, ValueError):
        if strict:
            raise
        return result
    return result


def completed_tasks(
    root: Path, session_id: str, *, ignored: Collection[str] = (), strict: bool = False
) -> dict[str, str]:
    """Only root agent completions qualify for automatic followups."""
    return {
        key: task.status
        for key, task in task_snapshots(root, session_id, ignored=ignored, strict=strict).items()
        if task.kind == "agent" and task.terminal
    }
