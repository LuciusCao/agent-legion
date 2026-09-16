"""Studio chat context-health signals (#694): usage mirror, compaction
window tracking + send guard, degenerate-turn detection, session/load
replay suppression.

Drives the service callbacks directly (stub handle, no subprocess), the
same pattern as test_studio_chat_service_sessions.py's _direct_session.
"""

from __future__ import annotations

import json
import time

import pytest

from server.app.services.job_errors import ConflictError
from server.app.studio_chat import compaction
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.service import StudioChatService
from server.app.studio_chat.session_config_state import OpenedAcpSession


class RecordingBus:
    """EventBus stand-in capturing published (channel, payload) pairs."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def attach_loop(self, loop) -> None:
        del loop

    def publish(self, channel: str, payload: str, *, replaceable: bool = False) -> None:
        self.events.append((channel, json.loads(payload)))

    def subscribe(self, channel: str):
        raise NotImplementedError

    def unsubscribe(self, channel: str, queue) -> None:
        del channel, queue


class _StubHandle:
    def send_prompt(self, text: str) -> bool:
        del text
        return True

    def cancel(self) -> None: ...

    def close(self) -> None: ...


@pytest.fixture
def direct(job_db, settings):
    """Idle session row + registered runtime without an ACP subprocess."""
    bus = RecordingBus()
    service = StudioChatService(job_db, settings, bus)
    workspace_id = job_db.create_workspace(default_workflow_key="demo_workflow", name="Chat WS")[
        "id"
    ]
    user_id = str(job_db.create_user("chat-user", password_hash=None)["id"])
    session_id = job_db.create_studio_chat_session(workspace_id, user_id, "direct-agent")
    job_db.update_studio_chat_session(session_id, status="idle")
    runtime = SessionRuntime(_StubHandle(), token="direct-token")
    with service._runtimes_lock:
        service._runtimes[session_id] = runtime
    yield service, bus, session_id, runtime, workspace_id
    service.shutdown()


def _chunk(text: str) -> dict:
    return {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": text},
    }


def _ready(service, session_id: str) -> None:
    service._on_ready(
        session_id,
        {"loadSession": True},
        OpenedAcpSession("acp-1", True, None, None),
    )


def _status_events(service, session_id: str, workspace_id: str) -> list[dict]:
    return [
        m["content"]
        for m in service.list_messages(session_id, workspace_id)
        if m["kind"] == "status"
    ]


def _agent_texts(service, session_id: str, workspace_id: str) -> list[str]:
    return [
        m["content"]["text"]
        for m in service.list_messages(session_id, workspace_id)
        if m["kind"] == "text" and m["role"] == "agent"
    ]


def test_usage_update_mirrors_to_session_row_and_sse(direct) -> None:
    service, bus, session_id, _runtime, workspace_id = direct
    try:
        service._on_update(
            session_id, {"sessionUpdate": "usage_update", "used": 12345, "size": 262144}
        )
        assert service.get_session(session_id)["usage"] == {"used": 12345, "size": 262144}
        session_payloads = [p for _, p in bus.events if p.get("type") == "session"]
        assert session_payloads[-1]["session"]["usage"] == {"used": 12345, "size": 262144}
        # usage_update never lands on the message timeline.
        assert service.list_messages(session_id, workspace_id) == []
    finally:
        service.shutdown()


def test_malformed_usage_update_is_ignored(direct) -> None:
    service, _bus, session_id, _runtime, _workspace_id = direct
    try:
        service._on_update(session_id, {"sessionUpdate": "usage_update", "used": 10})
        assert service.get_session(session_id)["usage"] is None
    finally:
        service.shutdown()


def test_compact_markers_become_status_messages_and_flag(direct) -> None:
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        # The marker is consumed: no agent text row, one visible status row,
        # and the flag is mirrored on both the runtime and the session row.
        assert _agent_texts(service, session_id, workspace_id) == []
        events = _status_events(service, session_id, workspace_id)
        assert [e["event"] for e in events] == ["compact_start"]
        assert runtime.compacting is True
        assert service.get_session(session_id)["compacting"] is True

        service._on_update(
            session_id,
            _chunk("Compaction completed.\n- Tokens before: 200,000\n- Tokens after: 80,000"),
        )
        events = _status_events(service, session_id, workspace_id)
        assert [e["event"] for e in events] == ["compact_start", "compact_done"]
        assert events[1]["detail"].startswith("Compaction completed.")
        assert runtime.compacting is False
        assert service.get_session(session_id)["compacting"] is False
    finally:
        service.shutdown()


def test_duplicate_compact_start_marker_does_not_repeat_the_notice(direct) -> None:
    service, _bus, session_id, _runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        events = _status_events(service, session_id, workspace_id)
        assert [e["event"] for e in events] == ["compact_start"]
    finally:
        service.shutdown()


def test_send_is_refused_while_compacting_but_compact_command_passes(direct) -> None:
    service, _bus, session_id, _runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        with pytest.raises(ConflictError, match="正在压缩上下文"):
            service.send_message(session_id, workspace_id, "hello")
        # The refused send never claimed the turn nor wrote a user message.
        assert service.get_session(session_id)["status"] == "idle"
        assert service.list_messages(session_id, workspace_id) == [
            m for m in service.list_messages(session_id, workspace_id) if m["kind"] != "text"
        ]
        # /compact itself is never intercepted.
        service.send_message(session_id, workspace_id, "/compact")
        assert service.get_session(session_id)["status"] == "running"
    finally:
        service.shutdown()


def test_stale_compacting_flag_self_clears_on_send(direct, job_db, monkeypatch) -> None:
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        monkeypatch.setattr(compaction, "COMPACTING_TIMEOUT_SECONDS", 1)
        with runtime.lock:
            runtime.compacting_since = time.monotonic() - 10
        service.send_message(session_id, workspace_id, "hello again")
        assert service.get_session(session_id)["status"] == "running"
        assert runtime.compacting is False
        assert job_db.get_studio_chat_session(session_id)["compacting"] is False
    finally:
        service.shutdown()


def test_on_ready_clears_inherited_compacting_flag(direct) -> None:
    service, _bus, session_id, runtime, _workspace_id = direct
    try:
        with runtime.lock:
            runtime.compacting = True
        service._db.update_studio_chat_session(session_id, compacting=True)
        _ready(service, session_id)
        assert runtime.compacting is False
        assert runtime.loading is False
        assert service.get_session(session_id)["compacting"] is False
    finally:
        service.shutdown()


def test_instant_zero_content_end_turn_is_flagged(direct) -> None:
    service, _bus, session_id, _runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service.send_message(session_id, workspace_id, "hello")
        service._on_turn_end(session_id, "end_turn")
        events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
        # (第一轮完成还会触发一次性的 mcp_unverified 提示，不参与断言。)
        assert "empty_turn" in events and "turn_end" in events
        assert events.index("empty_turn") < events.index("turn_end")
        assert service.get_session(session_id)["status"] == "idle"
    finally:
        service.shutdown()


def test_turn_with_content_is_not_flagged(direct) -> None:
    service, _bus, session_id, _runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service.send_message(session_id, workspace_id, "hello")
        service._on_update(session_id, _chunk("短回复"))
        service._on_turn_end(session_id, "end_turn")
        events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
        assert "empty_turn" not in events and "turn_end" in events
    finally:
        service.shutdown()


def test_slow_or_slash_turns_are_not_flagged(direct) -> None:
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        # A zero-content turn that took longer than the threshold is a legal
        # (if odd) answer, not the quiescence-window signature.
        service.send_message(session_id, workspace_id, "think quietly")
        with runtime.lock:
            runtime.turn_started_at = time.monotonic() - 10
        service._on_turn_end(session_id, "end_turn")
        # Slash commands are local by design: /compact settles instantly with
        # no agent content and must not be misread as a fake completion.
        service.send_message(session_id, workspace_id, "/compact")
        service._on_turn_end(session_id, "end_turn")
        events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
        assert "empty_turn" not in events
        assert events.count("turn_end") == 2
    finally:
        service.shutdown()


def test_replay_chunks_during_session_load_are_not_persisted(direct) -> None:
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        # Runtime starts in the loading window (session/load replay).
        assert runtime.loading is True
        service._on_update(session_id, _chunk("历史消息一"))
        service._on_update(session_id, _chunk("历史消息二"))
        assert _agent_texts(service, session_id, workspace_id) == []
        _ready(service, session_id)
        service.send_message(session_id, workspace_id, "hello")
        service._on_update(session_id, _chunk("新回复"))
        assert _agent_texts(service, session_id, workspace_id) == ["新回复"]
    finally:
        service.shutdown()
