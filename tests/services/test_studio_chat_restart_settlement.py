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


def _seed_early_shapes(db, workspace: str, user: str, total: int) -> tuple[str, str, str, str]:
    sid = db.create_studio_chat_session(workspace, user, "fake-agent")
    token = mint_scoped_token(db, user, origin="run", workspace_id=workspace)
    db.update_studio_chat_session(sid, status="idle")
    _history(db, sid, token)
    _pending(db, sid)
    direct = db.accept_studio_chat_message(sid, hash_token(token), "early direct unknown")
    delivered = db.enqueue_studio_chat_message(sid, hash_token(token), "early queued unknown")
    dropped = db.enqueue_studio_chat_message(sid, hash_token(token), "early queued dropped")
    db.update_studio_chat_session(sid, status="idle")
    notice = {
        "event": "queued_delivered",
        "message_id": delivered["id"],
        "detail": "排队消息已开始处理",
    }
    db.claim_studio_chat_turn_with_token(sid, hash_token(token), notice)
    current = len(db.list_studio_chat_messages(sid))
    for index in range(current, total):
        db.append_studio_chat_message(sid, "status", "system", {"event": "noise", "index": index})
    assert len(db.list_studio_chat_messages(sid, limit=total + 1)) == total
    return sid, direct["id"], delivered["id"], dropped["id"]


@pytest.mark.parametrize("total", [500, 501, 1000, 1001])
def test_startup_replays_every_message_page(chat, total: int) -> None:
    service, _bus, _register, workspace, user = chat
    sid, direct_id, delivered_id, dropped_id = _seed_early_shapes(
        service.db, workspace, user, total
    )

    service.reap_zombie_sessions()
    service.reap_zombie_sessions()

    resolved = [
        m
        for m in service.db.list_studio_chat_messages(sid)
        if m["kind"] == "permission" and m["content"].get("status") == "resolved"
    ]
    unknown = [
        event for event in _events(service.db, sid, "error") if event.get("outcome") == "unknown"
    ]
    dropped = _events(service.db, sid, "queued_dropped")
    assert len(resolved) == 1
    assert {event["message_id"] for event in unknown} == {direct_id, delivered_id}
    assert len(unknown) == 2
    assert [event["message_id"] for event in dropped] == [dropped_id]
    assert service.db.get_studio_chat_session(sid)["status"] == "error"


def test_forward_reader_does_not_change_latest_page_contract(chat) -> None:
    service, _bus, _register, workspace, user = chat
    sid, _direct_id, _delivered_id, _dropped_id = _seed_early_shapes(
        service.db, workspace, user, 501
    )

    latest, has_more = service.db.list_studio_chat_messages_page(sid, limit=500)
    forward = service.db.list_studio_chat_messages_forward(sid, limit=500)

    assert len(latest) == len(forward) == 500
    assert has_more is True
    assert latest[0]["seq"] > forward[0]["seq"]


def test_legacy_delivered_dropped_is_unknown_and_never_replayed(chat) -> None:
    service, _bus, register, workspace, user = chat
    script_path = register(fixtures.TEXT_SCRIPT)
    sid, unknown_text, dropped_text = _seed_queued(service.db, workspace, user)
    delivered_id = next(
        m["id"]
        for m in service.db.list_studio_chat_messages(sid)
        if m["kind"] == "text" and m["content"].get("text") == unknown_text
    )
    service.db.append_studio_chat_message(
        sid,
        "status",
        "system",
        {
            "event": "queued_dropped",
            "message_id": delivered_id,
            "detail": "legacy post-commit drop",
        },
    )

    service.reap_zombie_sessions()
    service.reap_zombie_sessions()

    unknown = [
        event
        for event in _events(service.db, sid, "error")
        if event.get("message_id") == delivered_id and event.get("outcome") == "unknown"
    ]
    dropped = [
        event
        for event in _events(service.db, sid, "queued_dropped")
        if event["message_id"] == delivered_id
    ]
    assert len(unknown) == len(dropped) == 1
    restarted = StudioChatService(service.db, service._settings, service._bus)
    _resume_and_check(restarted, script_path, sid, workspace, user, [unknown_text, dropped_text])


def _idle_session(db, workspace: str, user: str) -> str:
    sid = db.create_studio_chat_session(workspace, user, "fake-agent")
    db.update_studio_chat_session(sid, status="idle")
    return sid


def test_startup_excludes_deleted_and_archived_candidates(chat) -> None:
    service, _bus, _register, workspace, user = chat
    deleted = _idle_session(service.db, workspace, user)
    archived = _idle_session(service.db, workspace, user)
    live = _idle_session(service.db, workspace, user)
    assert service.db.mark_studio_chat_session_deleted(deleted)
    assert service.db.set_studio_chat_session_archived(archived, True)

    service.reap_zombie_sessions()

    assert service.db.get_studio_chat_session(live)["status"] == "error"
    assert service.db.get_studio_chat_session(deleted)["status"] == "idle"
    assert service.db.get_studio_chat_session(archived)["status"] == "idle"
    assert {row["id"] for row in service.db.list_zombie_studio_chat_sessions()} == {live}


@pytest.mark.parametrize("stamp", ["deleted", "archived"])
def test_startup_visibility_cas_blocks_delete_or_archive_after_read(
    chat, monkeypatch, stamp: str
) -> None:
    service, _bus, _register, workspace, user = chat
    sid = _idle_session(service.db, workspace, user)
    real_list = service.db.list_zombie_studio_chat_sessions

    def hide_after_read():
        rows = real_list()
        if stamp == "deleted":
            assert service.db.mark_studio_chat_session_deleted(sid)
        else:
            assert service.db.set_studio_chat_session_archived(sid, True)
        return rows

    monkeypatch.setattr(service.db, "list_zombie_studio_chat_sessions", hide_after_read)
    service.reap_zombie_sessions()

    row = service.db.get_studio_chat_session(sid)
    assert row["status"] == "idle"
    assert row[f"{stamp}_at"] is not None
