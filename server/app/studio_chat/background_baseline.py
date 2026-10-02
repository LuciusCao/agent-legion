"""Freeze historical completions before attempting to restore an ACP session."""

import logging
from dataclasses import dataclass
from pathlib import Path

from server.app.studio_chat.kimi_task_store import completed_tasks, task_root

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletionBaseline:
    root: Path
    acp_session_id: str
    finished: frozenset[str]


def capture_resume_baseline(cwd: str, acp_session_id: str | None) -> CompletionBaseline | None:
    if not acp_session_id or (root := task_root(cwd, acp_session_id)) is None:
        return None
    try:
        return CompletionBaseline(
            root, acp_session_id, frozenset(completed_tasks(root, acp_session_id, strict=True))
        )
    except (OSError, ValueError):
        logger.warning("Kimi resume baseline unavailable; defer to watcher", exc_info=True)
        return None
