"""Session spawn surfaces an unreachable api_base as a timeline warning (#915).

Create and resume share spawn.py: both must run the token-less self-check and,
when api_base does not call back into this instance, append one actionable
``mcp_callback_unreachable`` status row — without blocking the session (the
agent can still chat; it just has no platform tools).
"""

from __future__ import annotations

from server.app.studio_chat import spawn as spawn_module
from server.app.studio_chat.callback_check import (
    CALLBACK_UNREACHABLE_EVENT,
    warn_callback_unreachable,
)
from tests.helpers import studio_chat_fixtures

chat = studio_chat_fixtures.chat
TEXT_SCRIPT = studio_chat_fixtures.TEXT_SCRIPT


def _status_rows(service, session_id: str, workspace_id: str) -> list[dict]:
    return [
        m["content"]
        for m in service.list_messages(session_id, workspace_id)
        if m["kind"] == "status"
    ]


def test_unreachable_api_base_warns_on_create_and_resume_without_blocking(
    chat, monkeypatch
) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    probed: list[str] = []

    def unreachable(api_base: str) -> str:
        probed.append(api_base)
        return "连接失败（ConnectError）"

    monkeypatch.setattr(spawn_module, "check_api_base", unreachable)

    session = service.create_session(workspace_id, user_id, "fake-agent")
    assert session["status"] == "idle"
    rows = _status_rows(service, session["id"], workspace_id)
    warnings = [r for r in rows if r.get("event") == CALLBACK_UNREACHABLE_EVENT]
    assert len(warnings) == 1
    assert "http://127.0.0.1:8000" in warnings[0]["detail"]
    assert "平台回调地址" in warnings[0]["detail"]
    # The self-check probes exactly the address injected into the MCP entry.
    assert probed == ["http://127.0.0.1:8000"]

    service.close_session(session["id"], workspace_id)
    resumed = service.resume_session(session["id"], workspace_id, user_id)
    assert resumed["status"] == "idle"
    events = [r.get("event") for r in _status_rows(service, session["id"], workspace_id)]
    assert events.count(CALLBACK_UNREACHABLE_EVENT) == 2


def test_reachable_api_base_adds_no_warning(chat) -> None:
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)  # the fixture pins the self-check to "reachable"
    session = service.create_session(workspace_id, user_id, "fake-agent")
    events = [r.get("event") for r in _status_rows(service, session["id"], workspace_id)]
    assert CALLBACK_UNREACHABLE_EVENT not in events


def _warning_events(bus, service, session_id: str, workspace_id: str) -> tuple[list, list]:
    rows = [
        r
        for r in _status_rows(service, session_id, workspace_id)
        if r.get("event") == CALLBACK_UNREACHABLE_EVENT
    ]
    published = [
        payload
        for _channel, payload in bus.events
        if payload.get("type") == "message"
        and payload["message"]["content"].get("event") == CALLBACK_UNREACHABLE_EVENT
    ]
    return rows, published


def test_warning_skips_a_session_closed_after_startup(chat) -> None:
    """codex P2 (#920): a close landing between startup and the warning write
    owns the final state — no row, no published event."""
    service, bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    service.close_session(session["id"], workspace_id)
    bus.events.clear()
    warn_callback_unreachable(service.store, session["id"], "http://x", "连接失败（ConnectError）")
    assert _warning_events(bus, service, session["id"], workspace_id) == ([], [])


def test_warning_skips_a_soft_deleted_session_with_live_status(chat, job_db) -> None:
    """Soft delete stamps deleted_at before the runtime is retired, so the row
    can still say idle: the deleted_at predicate alone must block the write."""
    service, bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    assert job_db.mark_studio_chat_session_deleted(session["id"])
    assert job_db.get_studio_chat_session(session["id"])["status"] == "idle"
    bus.events.clear()
    warn_callback_unreachable(service.store, session["id"], "http://x", "连接失败（ConnectError）")
    messages = job_db.list_studio_chat_messages(session["id"])
    assert not any(m["content"].get("event") == CALLBACK_UNREACHABLE_EVENT for m in messages)
    assert not [p for _c, p in bus.events if p.get("type") == "message"]


def test_warning_lands_and_publishes_for_a_live_session(chat) -> None:
    service, bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session = service.create_session(workspace_id, user_id, "fake-agent")
    bus.events.clear()
    warn_callback_unreachable(service.store, session["id"], "http://x", "连接失败（ConnectError）")
    rows, published = _warning_events(bus, service, session["id"], workspace_id)
    assert len(rows) == 1 and len(published) == 1
