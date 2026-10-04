"""Kimi task completion cursor, cancellation epochs and idle followups."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from server.app.studio_chat.background_delivery import wake_session
from server.app.studio_chat.background_rearm import prepare_rearm as prepare_rearm
from server.app.studio_chat.background_rearm import rearm_wakeup as rearm_wakeup
from server.app.studio_chat.background_rearm import try_rearm
from server.app.studio_chat.background_receipts import ReceiptCursor
from server.app.studio_chat.kimi_task_store import task_root, task_snapshots

if TYPE_CHECKING:
    from pathlib import Path

    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)
POLL_SECONDS = 2


class CompletionCursor:
    def __init__(
        self, root: Path, acp_session_id: str, *, seen: frozenset[str] | None = None
    ) -> None:
        self.root, self.acp_session_id = root, acp_session_id
        self.seen = set(seen or ())
        self.pending: set[str] = set()
        self.initialized = seen is not None
        self.receipts = ReceiptCursor.from_baseline(root, acp_session_id, self.seen)
        if not self.initialized:
            try:
                self.baseline()
            except (OSError, ValueError):
                logger.warning(
                    "Kimi initial baseline unavailable; watcher will retry", exc_info=True
                )

    def baseline(self) -> None:
        tasks = task_snapshots(self.root, self.acp_session_id, strict=True)
        if not self.initialized:
            self.receipts.initial_terminal.update(
                key for key, task in tasks.items() if task.terminal
            )
        self.seen.update(
            key for key, task in tasks.items() if task.kind == "agent" and task.terminal
        )
        self.pending.clear()
        self.initialized = True

    def step(self, service: StudioChatService, session_id: str, runtime: SessionRuntime) -> None:
        # Scan and cancellation baseline share the lock: stale scan results
        # cannot cross a rapid cancel/rearm boundary.
        with runtime.lock:
            if runtime.closed or service.runtime(session_id) is not runtime:
                return
            if not self.initialized:
                self.baseline()
            completed = self.receipts.step(service, session_id)
            if runtime.background_wakeup_enabled:
                self.pending.update(completed - self.seen)
            self.seen.update(completed)
            if runtime.background_cleanup is not None:
                if not runtime.background_cleanup():
                    return
                runtime.background_cleanup = None
            if not runtime.background_wakeup_enabled:
                if runtime.background_rearm_epoch is None:
                    self.baseline()
                    return
                if not try_rearm(runtime):
                    return
            if self.pending and wake_session(service, session_id, runtime, sorted(self.pending)):
                self.pending.clear()


def cancel_wakeup(runtime: SessionRuntime) -> None:
    with runtime.lock:
        runtime.background_epoch += 1
        runtime.background_rearm_epoch = None
        runtime.background_wakeup_enabled = False
        if runtime.background_cursor is not None:
            runtime.background_cursor.pending.clear()


def start_watcher(
    service: StudioChatService, session_id: str, runtime: SessionRuntime, acp_session_id: str
) -> None:
    if not runtime.kimi_agent:
        return
    root = task_root(runtime.handle.cwd, acp_session_id)
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
        cursor = runtime.background_cursor = CompletionCursor(root, acp_session_id, seen=seen)

    def watch() -> None:
        while not runtime.background_stop.wait(POLL_SECONDS):
            try:
                cursor.step(service, session_id, runtime)
            except Exception:
                # #204 broad-except audit: retry metadata/DB failures without
                # dropping pending completions or killing chat; retain traceback.
                logger.warning("Kimi completion watcher failed for %s", session_id, exc_info=True)

    threading.Thread(target=watch, name="studio-kimi-completions", daemon=True).start()
