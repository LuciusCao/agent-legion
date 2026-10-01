"""Read-only Kimi background-task V1 compatibility boundary (#806).

Kimi ACP drains notifications only inside prompt(). Its task store is the
out-of-turn completion signal: metadata.WorkDirMeta.sessions_dir and
background/{models,store}.py in MoonshotAI/kimi-cli define this layout.
Never inspect outputs, mutate consumer state, or traverse other sessions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Collection
from pathlib import Path
from typing import Any

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


def _read(path: Path, root: Path) -> dict[str, Any]:
    if path.resolve() != path or not path.is_relative_to(root):
        return {}
    if not stat.S_ISREG(path.lstat().st_mode):
        return {}
    # NONBLOCK also protects against a FIFO swapped in after lstat; fstat
    # validates the opened object, and NOFOLLOW rejects a swapped symlink.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return {}
        data = os.read(descriptor, 65537)
    finally:
        os.close(descriptor)
    if len(data) > 65536:
        return {}
    value = json.loads(data)
    return value if isinstance(value, dict) else {}


def completed_tasks(
    root: Path, session_id: str, *, ignored: Collection[str] = ()
) -> dict[str, str]:
    """Only root-owned agent tasks in this ACP session can trigger a wakeup.

    Missing/partial/unsupported files are not proof of completion. The
    bounded read excludes large tool output and corrupt runtime payloads.
    """
    result: dict[str, str] = {}
    try:
        if root.resolve() != root or not root.is_dir():
            return result
        for path in root.iterdir():
            if path.name in ignored or not _ID.fullmatch(path.name) or not path.is_dir():
                continue
            try:
                spec = _read(path / "spec.json", root)
                if (
                    spec.get("version") != 1
                    or spec.get("id") != path.name
                    or spec.get("session_id") != session_id
                    or spec.get("kind") != "agent"
                    or spec.get("owner_role", "root") != "root"
                ):
                    continue
                state = _read(path / "runtime.json", root)
                status = state.get("status")
                if isinstance(status, str) and status in TERMINAL:
                    result[path.name] = "timed_out" if state.get("timed_out") else status
            except (OSError, ValueError):
                continue
    except OSError:
        return result
    return result
