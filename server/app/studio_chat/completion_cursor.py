"""Kimi task completion cursor: durable receipts and idle followups.

Split from background_wakeup.py (budget, #972), which keeps the watcher
lifecycle and cancellation epochs and re-exports this class.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from server.app.studio_chat.background_delivery import wake_session
from server.app.studio_chat.background_rearm import try_rearm
from server.app.studio_chat.background_receipts import ReceiptCursor
from server.app.studio_chat.kimi_task_store import task_snapshots

if TYPE_CHECKING:
    from pathlib import Path

    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)


class CompletionCursor:
    def __init__(
        self,
        root: Path,
        acp_session_id: str,
        *,
        seen: frozenset[str] | None = None,
        wakes: bool = True,
    ) -> None:
        self.root, self.acp_session_id = root, acp_session_id
        # Kimi Code opens its own turn on a task notification (#938/#972):
        # receipts only, never a Studio wakeup prompt.
        self.wakes = wakes
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
            if runtime.background_wakeup_enabled and self.wakes:
                self.pending.update(completed - self.seen)
            self.seen.update(completed)
            if not self.wakes:
                return
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
