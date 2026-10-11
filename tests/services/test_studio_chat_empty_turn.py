"""Studio chat degenerate-turn (empty_turn) verdict (#694, #863).

#863 hardening: the verdict is deferred past a grace so trailing content
(the ACP SDK resolves the prompt response before the last update handlers
run) clears it, and the "waiting for background compaction" wording is
reserved for kimi sessions that actually showed compaction.

Drives the service callbacks directly (stub handle, no subprocess), the
same pattern as test_studio_chat_compaction.py. The verdict timer is a
test-gated _GatedTimer (#1118): it fires only when the test releases it,
so "trailing content clears the verdict" never races wall-clock scheduling.
"""

from __future__ import annotations

import threading
import time

import pytest

from server.app.auth.scoped_tokens import mint_scoped_token
from server.app.studio_chat import empty_turn
from server.app.studio_chat.runtime import SessionRuntime
from server.app.studio_chat.service import StudioChatService
from server.app.studio_chat.session_config_state import OpenedAcpSession
from server.app.studio_chat.turn_state import open_turn


class _StubHandle:
    def send_prompt(self, text: str, *, accept=None) -> bool:
        del text
        if accept is not None:
            accept()
        return True

    def cancel(self) -> None: ...

    def close(self) -> None: ...


class _GatedTimer(threading.Timer):
    """Verdict timer that fires only when the test releases it (#1118).

    start() launches the thread as usual, but run() waits on the release
    event instead of the grace interval: the test delivers every callback
    first, then releases the verdict — no assertion depends on the runner
    scheduling the timer thread within a wall-clock grace. cancel()
    (teardown) also releases so the thread exits instead of hanging.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.released = threading.Event()

    def run(self):
        self.released.wait(30)
        if not self.finished.is_set():
            self.function(*self.args, **self.kwargs)
        self.finished.set()

    def cancel(self):
        super().cancel()
        self.released.set()

    def release(self):
        self.released.set()


@pytest.fixture
def direct(job_db, settings, monkeypatch):
    """Idle session row + registered runtime without an ACP subprocess."""
    monkeypatch.setattr(empty_turn, "_timer_class", _GatedTimer)
    service = StudioChatService(job_db, settings, None)
    workspace_id = job_db.create_workspace(name="Chat WS")["id"]
    user_id = str(job_db.create_user("chat-user", password_hash=None)["id"])
    session_id = job_db.create_studio_chat_session(workspace_id, user_id, "direct-agent")
    job_db.update_studio_chat_session(session_id, status="idle")
    runtime = SessionRuntime(
        _StubHandle(), token=mint_scoped_token(job_db, user_id, workspace_id=workspace_id)
    )
    with service._runtimes_lock:
        service._runtimes[session_id] = runtime
    yield service, session_id, runtime, workspace_id
    service.shutdown()


def _ready(service, session_id: str, agent_name: str) -> None:
    service._on_ready(
        session_id,
        {"loadSession": True, "agentInfo": {"name": agent_name}},
        OpenedAcpSession("acp-1", True, None, None),
    )


def _chunk(text: str) -> dict:
    return {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}


def _fire_verdict(runtime: SessionRuntime) -> None:
    """Release the armed verdict and wait for its thread to finish.

    Signal-driven: the gated timer's run() proceeds only after release(),
    so this returns exactly when _confirm has completed — no grace sleep."""
    timer = runtime.empty_turn_timer
    assert isinstance(timer, _GatedTimer)
    timer.release()
    timer.join(30)
    assert not timer.is_alive()


def _empty_turns(service, session_id: str, workspace_id: str) -> list[dict]:
    return [
        m["content"]
        for m in service.list_messages(session_id, workspace_id)
        if m["kind"] == "status" and m["content"].get("event") == "empty_turn"
    ]


def test_instant_zero_content_end_turn_is_flagged_neutrally(direct) -> None:
    service, session_id, runtime, workspace_id = direct
    _ready(service, session_id, "kimi-code-acp")
    service.send_message(session_id, workspace_id, "hello")
    service._on_turn_end(session_id, "end_turn")
    _fire_verdict(runtime)
    [notice] = _empty_turns(service, session_id, workspace_id)
    # kimi, but no compaction seen in this process: claim no cause.
    assert notice["compaction_suspected"] is False
    assert notice["detail"] == empty_turn.NEUTRAL_DETAIL
    assert "压缩" not in notice["detail"]
    assert service.get_session(session_id)["status"] == "idle"


def test_trailing_content_after_turn_end_clears_the_verdict(direct) -> None:
    """#863 scenario ①: the prompt response beats the update handlers, so
    on_turn_end sees zero content; the reply landing within the grace means
    the turn was processed — no empty_turn next to a real reply."""
    service, session_id, runtime, workspace_id = direct
    _ready(service, session_id, "kimi-code-acp")
    service.send_message(session_id, workspace_id, "hello")
    service._on_turn_end(session_id, "end_turn")
    service._on_update(session_id, _chunk("短回复"))
    _fire_verdict(runtime)
    assert _empty_turns(service, session_id, workspace_id) == []
    texts = [
        m["content"]["text"]
        for m in service.list_messages(session_id, workspace_id)
        if m["kind"] == "text" and m["role"] == "agent"
    ]
    assert texts == ["短回复"]


def test_trailing_tool_call_also_clears_the_verdict(direct) -> None:
    service, session_id, runtime, workspace_id = direct
    _ready(service, session_id, "codex-acp")
    service.send_message(session_id, workspace_id, "hello")
    service._on_turn_end(session_id, "end_turn")
    service._on_update(
        session_id,
        {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "read", "status": "completed"},
    )
    _fire_verdict(runtime)
    assert _empty_turns(service, session_id, workspace_id) == []


def test_non_kimi_agent_never_gets_compaction_wording(direct) -> None:
    service, session_id, runtime, workspace_id = direct
    _ready(service, session_id, "codex-acp")
    # A non-kimi agent's text that happens to look like a kimi marker is
    # ordinary prose (marker gate) and is no compaction evidence either.
    service._on_update(session_id, _chunk("Compacting conversation context\n"))
    service.send_message(session_id, workspace_id, "hello")
    service._on_turn_end(session_id, "end_turn")
    _fire_verdict(runtime)
    [notice] = _empty_turns(service, session_id, workspace_id)
    assert notice["compaction_suspected"] is False
    assert notice["detail"] == empty_turn.NEUTRAL_DETAIL


def test_kimi_with_compaction_evidence_keeps_compaction_wording(direct) -> None:
    service, session_id, runtime, workspace_id = direct
    _ready(service, session_id, "kimi-code-acp")
    service._on_update(session_id, _chunk("Compacting conversation context\n"))
    service._on_update(session_id, _chunk("Compaction completed."))
    service.send_message(session_id, workspace_id, "hello")
    service._on_turn_end(session_id, "end_turn")
    _fire_verdict(runtime)
    [notice] = _empty_turns(service, session_id, workspace_id)
    assert notice["compaction_suspected"] is True
    assert notice["detail"] == empty_turn.COMPACTION_DETAIL
    # A fresh process (resume) inherits no compaction evidence.
    _ready(service, session_id, "kimi-code-acp")
    assert runtime.compaction_seen is False


def test_next_turn_inside_the_grace_drops_the_stale_verdict(direct) -> None:
    service, session_id, runtime, workspace_id = direct
    _ready(service, session_id, "kimi-code-acp")
    service.send_message(session_id, workspace_id, "hello")
    service._on_turn_end(session_id, "end_turn")
    service.send_message(session_id, workspace_id, "hello again")
    _fire_verdict(runtime)
    assert _empty_turns(service, session_id, workspace_id) == []


def test_slow_slash_and_platform_turns_are_not_flagged(direct) -> None:
    service, session_id, runtime, workspace_id = direct
    _ready(service, session_id, "kimi-code-acp")
    # A zero-content turn that took longer than the threshold is a legal
    # (if odd) answer, not the quiescence-window signature.
    service.send_message(session_id, workspace_id, "think quietly")
    with runtime.lock:
        runtime.turn_started_at = time.monotonic() - 10
    service._on_turn_end(session_id, "end_turn")
    assert runtime.empty_turn_timer is None
    # Slash commands are local by design: /compact settles instantly with
    # no agent content and must not be misread as a fake completion.
    service.send_message(session_id, workspace_id, "/compact")
    service._on_turn_end(session_id, "end_turn")
    assert runtime.empty_turn_timer is None
    # Background-task wakeups open with empty text: no user message to lose.
    with runtime.lock:
        open_turn(runtime, "", owner=object())
    service._on_turn_end(session_id, "end_turn")
    assert runtime.empty_turn_timer is None
    assert _empty_turns(service, session_id, workspace_id) == []


def test_teardown_cancels_a_pending_verdict(direct) -> None:
    service, session_id, runtime, workspace_id = direct
    _ready(service, session_id, "kimi-code-acp")
    service.send_message(session_id, workspace_id, "hello")
    service._on_turn_end(session_id, "end_turn")
    timer = runtime.empty_turn_timer
    assert isinstance(timer, _GatedTimer)
    service.shutdown()
    timer.join(30)
    assert not timer.is_alive()
    assert runtime.empty_turn_timer is None
