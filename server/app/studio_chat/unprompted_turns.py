"""Project agent-launched (unprompted) Kimi Code turns into the Studio timeline (#938).

A background-subagent completion or a cron fire makes Kimi Code's engine run a
turn with no ACP prompt in flight. Its ACP adapter drops that turn's events,
so Studio never received the agent's report: it surfaced only as context of
the next human turn. The watcher here tails the agent's wire journal
(kimi_wire.py) and persists each unprompted turn as ordinary timeline rows —
a visible receipt, agent text/thought, ACP-shaped tool cards and a closing
``turn_end`` — through the same store, so the SSE stream and every chat
surface render it live.

Turns the Studio drove (origin ``user`` / ``skill_activation``, including the
#816 wakeup prompt) already reach the timeline over ACP and are skipped. The
session status and turn ownership are untouched: a human message sent while
the agent reports is queued by the engine behind the unprompted turn, the
existing busy semantics of a running prompt.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from server.app.studio_chat.kimi_wire import WireTail, kimi_code_homes, locate_wire
from server.app.studio_chat.mcp_hint import is_agent_legion_tool_call
from server.app.studio_chat.wire_baseline import usable_baseline

if TYPE_CHECKING:
    from pathlib import Path

    from server.app.studio_chat.runtime import SessionRuntime
    from server.app.studio_chat.service import StudioChatService

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0
BOUND_ORIGINS = frozenset({"user", "skill_activation"})
_TASK_STATUS = {"completed": "已完成", "failed": "失败", "killed": "已终止", "lost": "已丢失"}
_TOOL_KINDS = {"Read": "read", "Glob": "read", "Grep": "read", "Write": "edit", "Edit": "edit"}
_TOOL_KINDS |= {"Bash": "execute", "WebFetch": "fetch", "WebSearch": "fetch", "Think": "think"}

# (kind, role, content) rows appended in order.
Row = tuple[str, str, dict[str, Any]]


def receipt_detail(origin: dict[str, Any]) -> str:
    kind = origin.get("kind")
    if kind == "task":
        status = str(origin.get("status") or "")
        label = _TASK_STATUS.get(status, status or "已结束")
        task = f" {origin['taskId']}" if origin.get("taskId") else ""
        return f"后台任务{task} {label}，agent 正在汇报"
    if kind == "cron_job":
        return "定时任务已触发，agent 正在处理"
    return "agent 自发开始了新一轮（非用户消息触发）"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _error_detail(error: Any) -> str:
    """Safe summary of a failed turn's error payload (kimi ``toErrorPayload``):
    code and message only — never ``details`` / ``cause``, which may carry
    request payloads."""
    payload = _mapping(error)
    code, message = payload.get("code"), payload.get("message")
    parts = [part for part in (f"[{code}]" if code else "", str(message or "")) if part]
    return ("agent 自发回合失败" + ("：" + " ".join(parts) if parts else ""))[:500]


def _text_blocks(value: Any) -> list[dict[str, Any]]:
    if value is None or value == "":
        return []
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return [{"type": "content", "content": {"type": "text", "text": text}}]


class UnpromptedTurnProjector:
    """Pure wire-record → timeline-row projection for unbound turns."""

    def __init__(self) -> None:
        self.turns: set[str] = set()
        self.tool_turns: dict[str, str] = {}

    def project(self, record: dict[str, Any]) -> list[Row]:
        if record.get("agentId") not in (None, "main"):
            return []
        kind = record.get("type")
        if kind == "turn.prompt":
            origin = _mapping(record.get("origin"))
            if origin.get("kind") in BOUND_ORIGINS or record.get("turnId") is None:
                return []
            self.turns.add(str(record["turnId"]))
            content = {
                "event": "unprompted_turn",
                "origin": str(origin.get("kind") or "unknown"),
                "detail": receipt_detail(origin),
            }
            return [("status", "system", content)]
        if kind == "turn.ended":
            turn = str(record.get("turnId"))
            if turn not in self.turns:
                return []
            self.turns.discard(turn)
            self.tool_turns = {k: v for k, v in self.tool_turns.items() if v != turn}
            reason = str(record.get("reason") or "")
            if reason == "failed":
                # Same shape as the ACP path's on_turn_error (events.on_error,
                # non-fatal): one error row, no turn_end, status untouched.
                err = {"event": "error", "detail": _error_detail(record.get("error"))}
                return [("status", "system", {**err, "unprompted": True})]
            stop = {"cancelled": "cancelled", "blocked": "refusal"}.get(reason, "end_turn")
            end: dict[str, Any] = {"event": "turn_end", "stop_reason": stop, "unprompted": True}
            return [("status", "system", end)]
        if kind == "context.append_loop_event" and isinstance(record.get("event"), dict):
            return self._loop_event(record["event"])
        return []

    def _loop_event(self, event: dict[str, Any]) -> list[Row]:
        kind = event.get("type")
        if kind == "tool.result":
            call_id = str(event.get("toolCallId") or "")
            turn = self.tool_turns.pop(call_id, None)
            if turn is None:
                return []
            result = _mapping(event.get("result"))
            output = result.get("output")
            update = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": f"{turn}:{call_id}",
                "status": "failed" if result.get("isError") is True else "completed",
                "rawOutput": output,
                "content": _text_blocks(output),
            }
            return [("tool_call", "agent", update)]
        turn = str(event.get("turnId"))
        if turn not in self.turns:
            return []
        if kind == "content.part" and isinstance(event.get("part"), dict):
            part = event["part"]
            if part.get("type") == "text" and part.get("text"):
                return [("text", "agent", {"text": str(part["text"])})]
            if part.get("type") == "think" and part.get("think"):
                return [("thought", "agent", {"text": str(part["think"])})]
            return []
        if kind == "tool.call":
            call_id, name = str(event.get("toolCallId") or ""), str(event.get("name") or "tool")
            self.tool_turns[call_id] = turn
            update = {
                "sessionUpdate": "tool_call",
                "toolCallId": f"{turn}:{call_id}",
                "title": str(event.get("description") or name),
                "kind": _TOOL_KINDS.get(name, "other"),
                "status": "in_progress",
                "rawInput": event.get("args"),
                "content": _text_blocks(event.get("args")),
            }
            return [("tool_call", "agent", update)]
        return []


class UnpromptedTurnWatcher:
    """Owns one runtime's journal tail; rows are written only while it is live."""

    def __init__(
        self,
        service: StudioChatService,
        session_id: str,
        runtime: SessionRuntime,
        locate: Callable[[], Path | None],
    ) -> None:
        self.service, self.session_id, self.runtime = service, session_id, runtime
        self.locate = locate
        self.tail: WireTail | None = None
        # Loaded journal without a usable pre-spawn baseline: baseline at
        # first sight (never replay); a journal this runtime created is read
        # from its start (WireTail.read, identity None).
        self.baseline_on_locate = False
        self.projector = UnpromptedTurnProjector()
        # Projected but not yet durably appended (retried on the next step,
        # before the journal is read any further).
        self.pending: list[Row] = []

    def step(self) -> None:
        if self.tail is None:
            path = self.locate()
            if path is None:
                return
            tail = WireTail(path)
            if self.baseline_on_locate:
                tail.baseline()
                self.tail = tail
                return
            self.tail = tail
        if not self.pending:
            # A backlog left by a failed append pauses the journal: it is
            # persisted first, so retries never grow it (#1044).
            for record in self.tail.read():
                self.pending.extend(self.projector.project(record))
        if not self.pending:
            return
        runtime, store = self.runtime, self.service.store
        with runtime.lock:
            # Delete/close fences (#900) retire the runtime under this lock
            # before touching the row: a closed runtime never writes again.
            if runtime.closed or self.service.runtime(self.session_id) is not runtime:
                self.pending.clear()
                return
            ended = False
            while self.pending:
                kind, role, content = self.pending[0]
                store.append_message(self.session_id, kind, role, content)
                self.pending.pop(0)
                if kind == "tool_call" and is_agent_legion_tool_call(content):
                    runtime.mcp_observed = True
                    store.mark_mcp_verified(self.session_id)
                ended = ended or content.get("event") == "turn_end"
        if ended:
            store.publish_session(self.session_id)


def start_unprompted_watcher(
    service: StudioChatService, session_id: str, runtime: SessionRuntime, acp_session_id: str
) -> None:
    if not runtime.kimi_agent:
        return
    homes = kimi_code_homes(runtime.handle.cwd)
    watcher = UnpromptedTurnWatcher(
        service, session_id, runtime, lambda: locate_wire(homes, acp_session_id)
    )
    # Where to start is decided by who wrote the journal, never by the clock
    # (#938 review R1/R2): a journal this runtime's process created is read
    # from its start; a loaded one continues from the baseline resume.py took
    # while no agent process existed, or is baselined at first sight.
    if runtime.handle.loaded_existing:
        if usable_baseline(runtime.wire_baseline, acp_session_id, watcher.locate()):
            watcher.tail = WireTail.from_baseline(runtime.wire_baseline)
        else:
            watcher.baseline_on_locate = True

    def watch() -> None:
        while True:
            try:
                watcher.step()
            except Exception:
                # #204 broad-except audit: journal I/O and DB failures retry on
                # the next poll (pending rows are kept); retain the traceback.
                logger.warning(
                    "Kimi unprompted-turn watcher failed for %s", session_id, exc_info=True
                )
            if runtime.background_stop.wait(POLL_SECONDS):
                return

    threading.Thread(target=watch, name="studio-kimi-unprompted", daemon=True).start()
