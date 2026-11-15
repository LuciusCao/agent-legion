"""Restart reconciliation from durable evidence, including post-commit faults."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.app.auth.scoped_tokens import mint_scoped_token
from server.app.auth.sessions import hash_token
from server.app.studio_chat.service import StudioChatService
from tests.helpers import studio_chat_fixtures as fixtures
from tests.helpers import wait_for_predicate

chat = fixtures.chat


def _prompt_texts(script_path: Path) -> list[str]:
    path = Path(str(script_path) + ".sink.jsonl")
    if not path.exists():
        return []
    return [
        "".join(block.get("text", "") for block in entry["received"]["params"].get("prompt", []))
        for entry in (json.loads(line) for line in path.read_text().splitlines())
        if entry.get("received", {}).get("method") == "session/prompt"
    ]


def _history(db, sid: str, token: str) -> None:
    db.accept_studio_chat_message(sid, hash_token(token), "known history")
    db.append_studio_chat_message(sid, "status", "system", {"event": "turn_end"})
    db.update_studio_chat_session(sid, status="idle")


def _pending(db, sid: str) -> None:
    db.append_studio_chat_message(
        sid,
        "permission",
        "agent",
        {"request_id": "restart-request", "status": "pending", "tool_call": {}, "options": []},
    )


def _seed_direct(db, workspace: str, user: str) -> tuple[str, str]:
    sid = db.create_studio_chat_session(workspace, user, "fake-agent")
    token = mint_scoped_token(db, user, origin="run", workspace_id=workspace)
    db.update_studio_chat_session(sid, status="idle")
    _history(db, sid, token)
    db.accept_studio_chat_message(sid, hash_token(token), "direct restart unknown")
    _pending(db, sid)
    db.update_studio_chat_session(sid, status="awaiting_permission")
    return sid, "direct restart unknown"


def _seed_queued(db, workspace: str, user: str) -> tuple[str, str, str]:
    sid = db.create_studio_chat_session(workspace, user, "fake-agent")
    token = mint_scoped_token(db, user, origin="run", workspace_id=workspace)
    db.update_studio_chat_session(sid, status="idle")
    _history(db, sid, token)
    assert db.claim_studio_chat_turn(sid)
    delivered = db.enqueue_studio_chat_message(sid, hash_token(token), "queued restart unknown")
    db.enqueue_studio_chat_message(sid, hash_token(token), "queued restart dropped")
    db.append_studio_chat_message(sid, "status", "system", {"event": "turn_end"})
    db.update_studio_chat_session(sid, status="idle")
    notice = {
        "event": "queued_delivered",
        "message_id": delivered["id"],
        "detail": "排队消息已开始处理",
    }
    db.claim_studio_chat_turn_with_token(sid, hash_token(token), notice)
    _pending(db, sid)
    db.update_studio_chat_session(sid, status="awaiting_permission")
    return sid, "queued restart unknown", "queued restart dropped"


def _inject_after_commit(service, db, stage: str, monkeypatch) -> None:
    if stage == "status":
        real_update = db.update_studio_chat_session_if
        failed = False

        def update_then_fail(*args, **kwargs):
            nonlocal failed
            result = real_update(*args, **kwargs)
            if kwargs.get("status") == "error" and not failed:
                failed = True
                raise RuntimeError("post-commit status failure")
            return result

        monkeypatch.setattr(db, "update_studio_chat_session_if", update_then_fail)
        return
    real_append = service.store.append_message_if_live
    failed = False

    def append_then_fail(*args, **kwargs):
        nonlocal failed
        result = real_append(*args, **kwargs)
        content = args[3]
        matched = (
            stage == "permission"
            and content.get("status") == "resolved"
            or stage == "dropped"
            and content.get("event") == "queued_dropped"
            or stage == "unknown"
            and content.get("outcome") == "unknown"
        )
        if matched and not failed:
            failed = True
            raise RuntimeError(f"post-commit {stage} failure")
        return result

    monkeypatch.setattr(service.store, "append_message_if_live", append_then_fail)


def _events(db, sid: str, name: str) -> list[dict]:
    return [
        m["content"]
        for m in db.list_studio_chat_messages(sid)
        if m["kind"] == "status" and m["content"].get("event") == name
    ]


def _assert_single_terminals(db, sid: str, *, queued: bool) -> None:
    resolved = [
        m
        for m in db.list_studio_chat_messages(sid)
        if m["kind"] == "permission" and m["content"].get("status") == "resolved"
    ]
    unknown = [event for event in _events(db, sid, "error") if event.get("outcome") == "unknown"]
    assert len(resolved) == 1
    assert resolved[0]["content"]["decision"] == {"deny": True, "via": "backend_restart"}
    assert len(unknown) == 1
    assert len(_events(db, sid, "queued_dropped")) == (1 if queued else 0)
    assert db.get_studio_chat_session(sid)["status"] == "error"


def _resume_and_check(
    service, script_path: Path, sid: str, workspace: str, user: str, old: list[str]
) -> None:
    try:
        assert service.resume_session(sid, workspace, user)["status"] == "idle"
        service.send_message(sid, workspace, "fresh after restart")
        wait_for_predicate(
            lambda: any(
                m["kind"] == "text" and m["role"] == "agent"
                for m in service.list_messages(sid, workspace)
            ),
            timeout=10,
        )
        prompt = _prompt_texts(script_path)[-1]
        assert "fresh after restart" in prompt
        assert "known history" in prompt
        for text in old:
            assert text not in prompt
    finally:
        service.shutdown()


@pytest.mark.parametrize("stage", ["permission", "unknown", "status"])
def test_direct_restart_recovers_from_each_commit_stage(chat, monkeypatch, stage: str) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register(fixtures.TEXT_SCRIPT)
    sid, unknown = _seed_direct(service.db, workspace, user)
    with monkeypatch.context() as patch:
        _inject_after_commit(service, service.db, stage, patch)
        with pytest.raises(RuntimeError, match="startup reconciliation failed"):
            service.reap_zombie_sessions()

    restarted = StudioChatService(service.db, service._settings, service._bus)
    restarted.reap_zombie_sessions()
    restarted.reap_zombie_sessions()
    _assert_single_terminals(service.db, sid, queued=False)
    _resume_and_check(restarted, script_path, sid, workspace, user, [unknown])


@pytest.mark.parametrize("stage", ["permission", "dropped", "unknown", "status"])
def test_queued_restart_recovers_from_each_commit_stage(chat, monkeypatch, stage: str) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register(fixtures.TEXT_SCRIPT)
    sid, unknown, dropped = _seed_queued(service.db, workspace, user)
    with monkeypatch.context() as patch:
        _inject_after_commit(service, service.db, stage, patch)
        with pytest.raises(RuntimeError, match="startup reconciliation failed"):
            service.reap_zombie_sessions()

    restarted = StudioChatService(service.db, service._settings, service._bus)
    restarted.reap_zombie_sessions()
    restarted.reap_zombie_sessions()
    _assert_single_terminals(service.db, sid, queued=True)
    _resume_and_check(restarted, script_path, sid, workspace, user, [unknown, dropped])
