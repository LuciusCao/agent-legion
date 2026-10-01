"""Read-only Kimi background-task V1 compatibility boundary (#806).

Kimi ACP drains notifications only inside prompt(). Its task store is the
out-of-turn completion signal: metadata.WorkDirMeta.sessions_dir and
background/{models,store}.py in MoonshotAI/kimi-cli define this layout.
Never inspect outputs, mutate consumer state, or traverse other sessions.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Collection
from pathlib import Path

from server.app.studio_chat.task_metadata_files import DIRECTORY_FLAGS, directory, read_json

TERMINAL = frozenset({"completed", "failed", "killed", "lost"})
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")


def task_root(cwd: str, session_id: str) -> Path | None:
    if not _ID.fullmatch(session_id):
        return None
    share = Path(os.environ.get("KIMI_SHARE_DIR", str(Path.home() / ".kimi")))
    if not share.is_absolute():
        share = Path(cwd) / share
    digest = hashlib.md5(str(Path(cwd).resolve()).encode(), usedforsecurity=False).hexdigest()
    return share.resolve() / "sessions" / digest / session_id / "tasks"


def completed_tasks(
    root: Path, session_id: str, *, ignored: Collection[str] = ()
) -> dict[str, str]:
    """Only root-owned agent tasks in this ACP session can trigger a wakeup.

    Missing/partial/unsupported files are not proof of completion. The
    bounded read excludes large tool output and corrupt runtime payloads.
    """
    result: dict[str, str] = {}
    try:
        with directory(root) as root_fd:
            for name in os.listdir(root_fd):
                if name in ignored or not _ID.fullmatch(name):
                    continue
                try:
                    task_fd = os.open(name, DIRECTORY_FLAGS, dir_fd=root_fd)
                    try:
                        spec = read_json(task_fd, "spec.json")
                        state = read_json(task_fd, "runtime.json")
                    finally:
                        os.close(task_fd)
                except (OSError, ValueError):
                    continue
                if (
                    spec.get("version") != 1
                    or spec.get("id") != name
                    or spec.get("session_id") != session_id
                    or spec.get("kind") != "agent"
                    or spec.get("owner_role", "root") != "root"
                ):
                    continue
                status = state.get("status")
                if isinstance(status, str) and status in TERMINAL:
                    result[name] = "timed_out" if state.get("timed_out") else status
    except (OSError, ValueError):
        return result
    return result
