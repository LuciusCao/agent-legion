"""Durable activity receipts have a separate cursor from automatic delivery."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from server.app.studio_chat.background_activity import BackgroundActivity
from server.app.studio_chat.background_recovery import recovery_sets
from server.app.studio_chat.kimi_task_store import task_snapshots

if TYPE_CHECKING:
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)


class ReceiptCursor:
    @classmethod
    def from_baseline(cls, root: Path, acp_session_id: str, seen: set[str]) -> ReceiptCursor:
        historical = seen | {
            key
            for key, task in task_snapshots(root, acp_session_id).items()
            if task.kind == "bash" and task.terminal
        }
        return cls(root, acp_session_id, historical)

    def __init__(self, root: Path, acp_session_id: str, initial_terminal: set[str]) -> None:
        self.root, self.acp_session_id = root, acp_session_id
        self.initial_terminal = initial_terminal
        self.seen: set[str] | None = None
        self.recovered: set[str] = set()
        self.activity = BackgroundActivity()

    def step(self, service: StudioChatService, session_id: str) -> set[str]:
        if self.seen is None:
            self.seen, self.recovered = recovery_sets(
                service.db, session_id, self.acp_session_id, self.initial_terminal
            )
        tasks = task_snapshots(self.root, self.acp_session_id, ignored=self.seen)
        ready: set[str] = set()
        for event in self.activity.updates(tasks, time.time()):
            event["acp_session_id"] = self.acp_session_id
            try:
                service.store.append_message(session_id, "status", "system", event)
            except Exception:
                # #204 broad-except audit: failed durable writes remain unrecorded
                # and retry next lap; isolate this task so other receipts and
                # completions can progress. Preserve the failure traceback.
                logger.warning(
                    "Background receipt failed for %s/%s",
                    session_id,
                    event["task_id"],
                    exc_info=True,
                )
                continue
            self.activity.recorded(event)
            if event["event"] == "background_task_finished":
                task_id = event["task_id"]
                self.seen.add(task_id)
                if event["kind"] == "agent" and task_id not in self.recovered:
                    ready.add(task_id)
        return ready
