"""Freeze historical completions before attempting to restore an ACP session."""

from dataclasses import dataclass
from pathlib import Path

from server.app.studio_chat.kimi_task_store import completed_tasks, task_root


@dataclass(frozen=True)
class CompletionBaseline:
    root: Path
    acp_session_id: str
    finished: frozenset[str]


def capture_resume_baseline(cwd: str, acp_session_id: str | None) -> CompletionBaseline | None:
    if not acp_session_id or (root := task_root(cwd, acp_session_id)) is None:
        return None
    return CompletionBaseline(
        root, acp_session_id, frozenset(completed_tasks(root, acp_session_id))
    )
