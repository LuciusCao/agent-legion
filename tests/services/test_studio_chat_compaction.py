"""Studio chat context-health signals (#694): usage mirror, compaction
window tracking + send guard, degenerate-turn detection, session/load
replay suppression.

Drives the service callbacks directly (stub handle, no subprocess), the
same pattern as test_studio_chat_service_sessions.py's _direct_session.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from server.app.services.job_errors import ConflictError
from server.app.studio_chat import compact_timer, compaction
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.service import StudioChatService
from server.app.studio_chat.session_config_state import OpenedAcpSession
from tests.helpers import wait_for_predicate


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
    # The direct-session pattern never goes through on_ready, which is where
    # the marker gate's kimi identity gets stamped (#694 review R2-P2) —
    # default the fixture to a kimi session; tests for the non-kimi gate
    # flip it back explicitly.
    runtime.kimi_agent = True
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
    # kimi identity rides the ACP agentInfo (kimi 0.42 reports "kimi-code-acp",
    # #694 review R2-P2): on_ready stamps the marker gate from it.
    service._on_ready(
        session_id,
        {"loadSession": True, "agentInfo": {"name": "kimi-code-acp"}},
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
        # spawn_session_runtime arms the window only for a session/load
        # attempt; simulate that state directly. The window outlives
        # on_ready (the SDK dispatches replay notifications asynchronously)
        # and closes at the first post-resume prompt.
        with runtime.lock:
            runtime.loading = True
        service._on_update(session_id, _chunk("历史消息一"))
        _ready(service, session_id)
        # Still inside the window after on_ready: late-dispatched replay
        # chunks stay suppressed.
        service._on_update(session_id, _chunk("历史消息二"))
        assert _agent_texts(service, session_id, workspace_id) == []
        service.send_message(session_id, workspace_id, "hello")
        service._on_update(session_id, _chunk("新回复"))
        assert _agent_texts(service, session_id, workspace_id) == ["新回复"]
    finally:
        service.shutdown()


def test_loading_window_is_armed_only_for_session_load_attempts() -> None:
    """#694 regression: a plain (non-resume) runtime must NOT suppress
    chunks — the trailing-chunk fold after turn_end depends on it."""
    runtime = SessionRuntime(_StubHandle(), token="t")
    assert runtime.loading is False


def test_replay_window_suppresses_markers_and_usage_before_classification(direct) -> None:
    """#694 review P2-b: replayed compaction markers must not rewrite the
    flag or append duplicate status messages on every resume; replayed
    usage is a stale historical mirror and is filtered too."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        with runtime.lock:
            runtime.loading = True
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        service._on_update(session_id, _chunk("Compaction completed.\n- Tokens after: 80,000"))
        service._on_update(
            session_id, {"sessionUpdate": "usage_update", "used": 999, "size": 262144}
        )
        assert _status_events(service, session_id, workspace_id) == []
        assert runtime.compacting is False
        session = service.get_session(session_id)
        assert session["compacting"] is False
        assert session["usage"] is None
        # After the window closes (first prompt), markers classify normally
        # again in a valid turn context (turn closed, #694 review R2-P2).
        service.send_message(session_id, workspace_id, "hello")
        service._on_turn_end(session_id, "end_turn")
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
        assert "compact_start" in events
        assert runtime.compacting is True
    finally:
        service.shutdown()


def test_compact_timeout_timer_self_clears_and_recovers_input(direct, monkeypatch) -> None:
    """#694 review P1: a lost completion marker must not dead-lock the input
    forever — the armed timer clears the flag, writes a visible status
    message, and the send path works again without any user action."""
    monkeypatch.setattr(compaction, "COMPACTING_TIMEOUT_SECONDS", 0.3)
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        assert runtime.compacting is True
        # The timer flips the runtime flag first and writes the DB row after;
        # wait on the DB row so the assertion cannot land between the two.
        wait_for_predicate(
            lambda: service.get_session(session_id)["compacting"] is False, timeout=10
        )
        events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
        assert events == ["compact_start", "compact_timeout"]
        # Input recovered: the send path no longer refuses.
        service.send_message(session_id, workspace_id, "恢复后的消息")
        assert service.get_session(session_id)["status"] == "running"
    finally:
        service.shutdown()


def test_compact_done_cancels_the_self_clear_timer(direct, monkeypatch) -> None:
    """A normal completion cancels the timer: no late compact_timeout notice
    fires into the timeline after the window already closed cleanly."""
    monkeypatch.setattr(compaction, "COMPACTING_TIMEOUT_SECONDS", 0.3)
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        service._on_update(session_id, _chunk("Compaction completed.\n- Tokens after: 80,000"))
        assert runtime.compact_timer is None
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            time.sleep(0.05)
        events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
        assert events == ["compact_start", "compact_done"]
    finally:
        service.shutdown()


def test_stale_timer_firing_does_not_clear_a_rearmed_window(direct) -> None:
    """Generation pinning: a timer from an older window (fired late, e.g. its
    cancel lost a race) must not clear the current window's flag."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        with runtime.lock:
            current_since = runtime.compacting_since
        compact_timer._fire(service, session_id, runtime, (current_since or 0) - 1)
        assert runtime.compacting is True
        assert service.get_session(session_id)["compacting"] is True
        events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
        assert events == ["compact_start"]
    finally:
        service.shutdown()


def test_send_late_compacting_flip_rolls_back_claim(direct, monkeypatch) -> None:
    """#694 review R2-P1: a compaction marker landing between the early
    send_blocked gate and the prompt hand-off must not slip the prompt into
    the quiescence window — the late re-check inside the turn-start
    critical section rolls the claim back and refuses with the same 409."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        sent: list[str] = []
        monkeypatch.setattr(
            runtime.handle,
            "send_prompt",
            lambda text: sent.append(text) or True,
        )

        def flipping_gate(db, sid, rt, text):
            # The ACP callback thread sets the flag right after the early
            # gate passes (the real path is compact_markers.apply_marker_gated).
            with rt.lock:
                rt.compacting = True
                rt.compacting_since = time.monotonic()
            return False

        monkeypatch.setattr(compaction, "send_blocked", flipping_gate)
        with pytest.raises(ConflictError, match="正在压缩上下文"):
            service.send_message(session_id, workspace_id, "hello")
        assert sent == []
        assert service.get_session(session_id)["status"] == "idle"
        # Like an early-gate refusal: no user message on the timeline.
        assert service.list_messages(session_id, workspace_id) == []
    finally:
        service.shutdown()


def test_send_late_expired_flag_clears_and_sends(direct, monkeypatch) -> None:
    """The late re-check keeps send_blocked's stale-flag semantics: a flag
    past the self-clear timeout is cleared and the send proceeds."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)

        def expired_gate(db, sid, rt, text):
            with rt.lock:
                rt.compacting = True
                rt.compacting_since = time.monotonic() - 10000
            return False

        monkeypatch.setattr(compaction, "send_blocked", expired_gate)
        service.send_message(session_id, workspace_id, "hello")
        assert service.get_session(session_id)["status"] == "running"
        assert runtime.compacting is False
    finally:
        service.shutdown()


def test_non_kimi_agent_marker_text_is_plain_text(direct) -> None:
    """#694 review R2-P2: a non-kimi agent's chunk starting with the marker
    prefix is ordinary prose — never swallowed, never flips the flag."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        with runtime.lock:
            runtime.kimi_agent = False
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        assert _agent_texts(service, session_id, workspace_id) == [
            "Compacting conversation context\n"
        ]
        assert _status_events(service, session_id, workspace_id) == []
        assert runtime.compacting is False
    finally:
        service.shutdown()


def test_in_turn_prose_with_marker_prefix_is_not_swallowed(direct) -> None:
    """#694 review R2-P2: inside a normal prompt turn, even a kimi agent's
    prose starting with the prefix is stream text — kimi only emits the
    local notices outside turns (or inside a /compact turn)."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service.send_message(session_id, workspace_id, "解释 compact 机制")
        service._on_update(session_id, _chunk("Compacting conversation context 的意思是压缩上下文"))
        assert _agent_texts(service, session_id, workspace_id) == [
            "Compacting conversation context 的意思是压缩上下文"
        ]
        assert _status_events(service, session_id, workspace_id) == []
        assert runtime.compacting is False
    finally:
        service.shutdown()


def test_manual_compact_turn_accepts_in_turn_markers(direct) -> None:
    """Manual /compact emits its markers in-turn: the turn-context condition
    must still recognize them (start in-turn, done after the turn closes)."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service.send_message(session_id, workspace_id, "/compact")
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        assert runtime.compacting is True
        service._on_turn_end(session_id, "end_turn")
        service._on_update(
            session_id,
            _chunk("Compaction completed.\n- Tokens before: 200,000\n- Tokens after: 80,000"),
        )
        assert runtime.compacting is False
        events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
        assert "compact_start" in events and "compact_done" in events
    finally:
        service.shutdown()


def test_on_ready_stamps_kimi_identity_for_the_marker_gate(direct) -> None:
    """The gate identity comes from the ACP agentInfo name OR the registry
    agent id (manual kimi-compatible registry rows keep working)."""
    service, _bus, session_id, runtime, _workspace_id = direct
    try:
        with runtime.lock:
            runtime.kimi_agent = False
        _ready(service, session_id)  # agentInfo "kimi-code-acp"
        assert runtime.kimi_agent is True

        with runtime.lock:
            runtime.kimi_agent = False
        service._on_ready(
            session_id,
            {"loadSession": True, "agentInfo": {"name": "fake-acp-agent"}},
            OpenedAcpSession("acp-1", True, None, None),
        )
        # agentInfo non-kimi and registry id "direct-agent" non-kimi.
        assert runtime.kimi_agent is False

        # agent_id 不在 session 更新白名单里：直接改行模拟手工注册的
        # kimi 兼容 agent（registry id 是门控身份的第二个来源）。
        with service._db.connect() as conn:
            conn.execute(
                "update studio_chat_sessions set agent_id='kimi' where id=%s", (session_id,)
            )
        service._on_ready(
            session_id,
            {"loadSession": True, "agentInfo": {"name": "fake-acp-agent"}},
            OpenedAcpSession("acp-1", True, None, None),
        )
        assert runtime.kimi_agent is True
    finally:
        service.shutdown()


def test_compact_command_inside_live_window_keeps_the_flag(direct) -> None:
    """/compact 豁免拦截但不得清掉 live 窗口：发送后窗口仍是 live 状态，
    后续普通消息仍被 409（窗口只能由 done 标记或超时自清关闭）。"""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        service.send_message(session_id, workspace_id, "/compact")
        assert runtime.compacting is True
        assert service.get_session(session_id)["compacting"] is True
        service._on_turn_end(session_id, "end_turn")
        with pytest.raises(ConflictError, match="正在压缩上下文"):
            service.send_message(session_id, workspace_id, "普通消息")
    finally:
        service.shutdown()


def test_marker_gate_is_reevaluated_inside_the_apply_critical_section(direct) -> None:
    """#694 review R3-P1: the gate is read under runtime.lock at apply time.
    A marker chunk that arrives during a /compact turn (gate-open context)
    but is applied only after the turn boundary flipped to a normal turn
    must be rejected as prose — the old shape (gate read before the lock)
    would have let it flip the flag."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service.send_message(session_id, workspace_id, "/compact")
        assert runtime.turn_open is True and runtime.turn_may_compact is True

        # Hold the lock so the marker thread blocks inside
        # apply_marker_gated; flip the turn context while it waits (the
        # /compact turn closed and a normal turn opened).
        with runtime.lock:
            marker_thread = threading.Thread(
                target=service._on_update,
                args=(session_id, _chunk("Compacting conversation context\n")),
            )
            marker_thread.start()
            runtime.turn_may_compact = False
            time.sleep(0.3)  # let the marker thread reach and block on the lock
        marker_thread.join(timeout=5)

        assert not marker_thread.is_alive()
        assert runtime.compacting is False
        assert _agent_texts(service, session_id, workspace_id) == [
            "Compacting conversation context\n"
        ]
        assert all(
            e["event"] != "compact_start" for e in _status_events(service, session_id, workspace_id)
        )
    finally:
        service.shutdown()


def test_stale_timer_after_resume_does_not_touch_the_new_runtime(direct) -> None:
    """#694 review R3-P2: a timer from the OLD runtime must not write into a
    session that resume re-homed to a new runtime — the registry identity
    re-check makes the whole firing a no-op (no flag clobber, no notice)."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        with runtime.lock:
            armed_since = runtime.compacting_since
        assert service.get_session(session_id)["compacting"] is True
        # Resume swaps the registry to a NEW runtime for the same session.
        new_runtime = SessionRuntime(_StubHandle(), token="new-token")
        with service._runtimes_lock:
            service._runtimes[session_id] = new_runtime
        compact_timer._fire(service, session_id, runtime, armed_since)
        # The row flag is untouched (still the old window's True; the real
        # resume path clears it via on_ready) and no stale notice lands.
        assert service.get_session(session_id)["compacting"] is True
        assert all(
            e["event"] != "compact_timeout"
            for e in _status_events(service, session_id, workspace_id)
        )
    finally:
        service.shutdown()


def test_timer_fire_with_already_closed_row_window_drops_the_notice(direct) -> None:
    """#694 review R3-P2: the conditional DB clear is the atomic arbiter —
    when the row no longer has the window open (cleared by resume's
    on_ready), the stale timeout notice is dropped, not appended."""
    service, _bus, session_id, runtime, workspace_id = direct
    try:
        _ready(service, session_id)
        service._on_update(session_id, _chunk("Compacting conversation context\n"))
        with runtime.lock:
            armed_since = runtime.compacting_since
        # The row's window is already closed (e.g. resume's on_ready ran).
        assert service._db.clear_studio_chat_compacting_if_set(session_id) is True
        compact_timer._fire(service, session_id, runtime, armed_since)
        # The in-memory flag was ours to clear, but the notice only rides a
        # successful conditional clear.
        assert runtime.compacting is False
        events = [e["event"] for e in _status_events(service, session_id, workspace_id)]
        assert events == ["compact_start"]
    finally:
        service.shutdown()
