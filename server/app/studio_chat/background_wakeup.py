"""Kimi task watcher lifecycle, cancellation epochs and idle followups.

Kimi CLI V1 sessions get receipts and idle followups; Kimi Code sessions
(located by their session directory, #972) get receipts only. The cursor
itself lives in completion_cursor.py.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from server.app.studio_chat.background_delivery import wake_session as wake_session
from server.app.studio_chat.background_rearm import prepare_rearm as prepare_rearm
from server.app.studio_chat.background_rearm import rearm_wakeup as rearm_wakeup
from server.app.studio_chat.completion_cursor import CompletionCursor
from server.app.studio_chat.kimi_code_tasks import kimi_code_task_root
from server.app.studio_chat.kimi_task_store import task_root

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)
POLL_SECONDS = 2


def cancel_wakeup(runtime: SessionRuntime) -> None:
    with runtime.lock:
        runtime.background_epoch += 1
        runtime.background_rearm_epoch = None
        runtime.background_wakeup_enabled = False
        if runtime.background_cursor is not None:
            runtime.background_cursor.pending.clear()


def _adopt_code_layout(
    runtime: SessionRuntime, cursor: CompletionCursor, acp_session_id: str
) -> CompletionCursor:
    """A V1 cursor that never observed its root switches to the Kimi Code
    layout once that session directory appears (#972)."""
    if not cursor.wakes or cursor.initialized:
        return cursor
    code_root = kimi_code_task_root(runtime.handle.cwd, acp_session_id)
    if code_root is None:
        return cursor
    with runtime.lock:
        if runtime.background_cursor is cursor:
            runtime.background_cursor = CompletionCursor(code_root, acp_session_id, wakes=False)
        return runtime.background_cursor or cursor


def start_watcher(
    service: StudioChatService, session_id: str, runtime: SessionRuntime, acp_session_id: str
) -> None:
    if not runtime.kimi_agent:
        return
    code_root = kimi_code_task_root(runtime.handle.cwd, acp_session_id)
    root = code_root or task_root(runtime.handle.cwd, acp_session_id)
    if root is None:
        return
    with runtime.lock:
        if runtime.closed or service.runtime(session_id) is not runtime:
            return
        if runtime.background_cursor is not None:
            return
        baseline = runtime.background_baseline
        seen = (
            baseline.finished
            if baseline is not None
            and runtime.handle.loaded_existing
            and baseline.root == root
            and baseline.acp_session_id == acp_session_id
            else None
        )
        cursor = runtime.background_cursor = CompletionCursor(
            root, acp_session_id, seen=seen, wakes=code_root is None
        )

    def watch() -> None:
        nonlocal cursor
        while not runtime.background_stop.wait(POLL_SECONDS):
            try:
                cursor = _adopt_code_layout(runtime, cursor, acp_session_id)
                cursor.step(service, session_id, runtime)
            except Exception:
                # #204 broad-except audit: retry metadata/DB failures without
                # dropping pending completions or killing chat; retain traceback.
                logger.warning("Kimi completion watcher failed for %s", session_id, exc_info=True)

    threading.Thread(target=watch, name="studio-kimi-completions", daemon=True).start()
