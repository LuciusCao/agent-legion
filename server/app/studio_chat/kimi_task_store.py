"""Read-only Kimi background-task V1 compatibility boundary (#806).

Kimi ACP drains notifications only inside prompt(). Its task store is the
out-of-turn completion signal: metadata.WorkDirMeta.sessions_dir and
background/{models,store}.py in MoonshotAI/kimi-cli define this layout.
Only bounded terminal output tails are read; never mutate consumer state
or traverse other sessions.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Collection
from pathlib import Path

from server.app.studio_chat.kimi_task_snapshot import (
    ACTIVE,
    TERMINAL,
    BackgroundTask,
    output_tail,
    read_state,
    timestamp,
)

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
    """Read only root-owned agent/bash tasks in this ACP session.

    Missing/partial/unsupported files are not proof of completion. The
    bounded read excludes large tool output and corrupt runtime payloads.
    """
    result: dict[str, BackgroundTask] = {}
    try:
        if root.resolve() != root or not root.is_dir():
            return result
        for path in root.iterdir():
            if path.name in ignored or not _ID.fullmatch(path.name) or not path.is_dir():
                continue
            try:
                spec = read_state(path / "spec.json", root)
                if (
                    spec.get("version") != 1
                    or spec.get("id") != path.name
                    or spec.get("session_id") != session_id
                    or spec.get("kind") not in ("agent", "bash")
                    or spec.get("owner_role", "root") != "root"
                ):
                    continue
                state = read_state(path / "runtime.json", root)
                status = state.get("status")
                if not isinstance(status, str) or status not in ACTIVE | TERMINAL:
                    continue
                terminal = status in TERMINAL
                output_at, summary = output_tail(path / "output.log", root, terminal=terminal)
                reason = state.get("failure_reason")
                if isinstance(reason, str) and reason:
                    summary = reason[:600]
                description = spec.get("description")
                result[path.name] = BackgroundTask(
                    path.name,
                    str(spec["kind"]),
                    "timed_out" if terminal and state.get("timed_out") is True else status,
                    description[:240] if isinstance(description, str) else path.name,
                    timestamp(state.get("started_at")) or timestamp(spec.get("created_at")),
                    timestamp(state.get("finished_at")),
                    timestamp(state.get("heartbeat_at")),
                    output_at,
                    summary,
                )
            except (OSError, ValueError):
                continue
    except OSError:
        return result
    return result


def completed_tasks(
    root: Path, session_id: str, *, ignored: Collection[str] = ()
) -> dict[str, str]:
    """Compatibility API: only agent completions qualify for auto-wakeup."""
    return {
        key: task.status
        for key, task in task_snapshots(root, session_id, ignored=ignored).items()
        if task.kind == "agent" and task.terminal
    }
