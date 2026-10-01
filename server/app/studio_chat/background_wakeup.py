"""Kimi completion bridge: poll small task-state files, never poll the model.

The watcher belongs to one runtime generation. Completed tasks get one
timeline receipt, then one idle→running claim delivers their ids to Kimi;
its next prompt drains the native completion notifications and summarizes
the results. Cancellation suppresses automatic followups until a human
sends again. Teardown stops the watcher and stale generations cannot send.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from server.app.studio_chat.background_activity import BackgroundActivity
from server.app.studio_chat.kimi_task_store import task_root, task_snapshots
from server.app.studio_chat.token_keepalive import keepalive_run_token

if TYPE_CHECKING:
    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)
POLL_SECONDS = 2


def wake_session(
    service: StudioChatService, session_id: str, runtime: SessionRuntime, task_ids: list[str]
) -> bool:
    """False retains pending completions while a human turn/compaction owns it."""
    with runtime.lock:
        if (
            service.runtime(session_id) is not runtime
            or runtime.closed
            or not runtime.background_wakeup_enabled
            or runtime.compacting
            or runtime.turn_open
        ):
            return False
        keepalive_run_token(service, session_id)
        if runtime.token_keepalive_done or not service.db.claim_studio_chat_turn(session_id):
            return False
        runtime.stream.reset()
        runtime.loading = False
        runtime.turn_open = True
        runtime.turn_started_at = time.monotonic()
        runtime.turn_update_count = 0
        runtime.turn_slash_command = False
        runtime.turn_may_compact = False
        prompt = (
            "[Studio 系统通知] 你先前派发的后台子代理任务已到终态："
            + ", ".join(task_ids)
            + "。请处理原生 completion notification，必要时用 TaskOutput 核对结果，"
            "完成验收并向用户汇报；不要重复派发这些任务。"
        )
        # Teardown can remove the registry entry before taking runtime.lock.
        # The claim checked durable state; close/shutdown share this lock.
        if service.runtime(session_id) is not runtime:
            runtime.turn_open = False
            return False
        if not runtime.handle.send_prompt(prompt):
            runtime.turn_open = False
            service.db.update_studio_chat_session_if(
                session_id, status_in=("running",), status="error"
            )
            service.store.publish_session(session_id)
            return False
        try:
            service.store.publish_session(session_id)
        except Exception:
            # #204 broad-except audit: the prompt is already queued, so a bus
            # failure must not replay it. The durable running row lets REST
            # refill recover the snapshot; keep the traceback for diagnosis.
            logger.warning("Kimi wakeup snapshot failed for %s", session_id, exc_info=True)
        return True


def start_watcher(
    service: StudioChatService, session_id: str, runtime: SessionRuntime, acp_session_id: str
) -> None:
    if not runtime.kimi_agent:
        return
    root = task_root(runtime.handle.cwd, acp_session_id)
    if root is None:
        return
    # Existing terminal history on resume is not a new completion.
    seen = {key for key, task in task_snapshots(root, acp_session_id).items() if task.terminal}
    activity = BackgroundActivity()

    def watch() -> None:
        pending: set[str] = set()
        while not runtime.background_stop.wait(POLL_SECONDS):
            try:
                tasks = task_snapshots(root, acp_session_id, ignored=seen)
                with runtime.lock:
                    if runtime.closed or service.runtime(session_id) is not runtime:
                        return
                    for event in activity.updates(tasks, time.time()):
                        service.store.append_message(
                            session_id,
                            "status",
                            "system",
                            event,
                        )
                        activity.recorded(event)
                        if event["event"] == "background_task_finished":
                            seen.add(event["task_id"])
                            if event["kind"] == "agent" and runtime.background_wakeup_enabled:
                                pending.add(event["task_id"])
                    if not runtime.background_wakeup_enabled:
                        pending.clear()
                    if pending and wake_session(service, session_id, runtime, sorted(pending)):
                        pending.clear()
            except Exception:
                # #204 broad-except audit: watcher retries read/DB/bus failures;
                # a receipt is marked seen only after append succeeds, pending
                # completions remain until delivery. Never kill the chat for a
                # compatibility watcher failure; retain traceback for diagnosis.
                logger.warning("Kimi completion watcher failed for %s", session_id, exc_info=True)

    threading.Thread(target=watch, name="studio-kimi-completions", daemon=True).start()
