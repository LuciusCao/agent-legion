"""Idle-state 「继续对话」 replays a confirmed empty turn's message once (#882).

The resume endpoint on a live idle session re-delivers the human message
whose turn the empty_turn verdict confirmed; it never duplicates the user
row and is a no-op once anything newer happened.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.app.auth.sessions import hash_token
from server.app.services.job_errors import ConflictError
from server.app.studio_chat import empty_turn
from tests.helpers import studio_chat_fixtures

admission = studio_chat_fixtures.admission


@pytest.fixture(autouse=True)
def _short_grace(monkeypatch):
    monkeypatch.setattr(empty_turn, "EMPTY_TURN_GRACE_SECONDS", 0.05)


def _settle_verdict(runtime) -> None:
    timer = runtime.empty_turn_timer
    if timer is not None:
        timer.join(5)
        assert not timer.is_alive()


def _empty_turn(service, sid: str, runtime) -> None:
    """The prompt settles instantly with zero content; the verdict confirms."""
    service._on_turn_end(sid, "end_turn")
    _settle_verdict(runtime)


def _drain(runtime) -> list[str]:
    items = []
    while not runtime.handle._queue.empty():
        item = runtime.handle._queue.get_nowait()
        if isinstance(item, (str, tuple)):  # skip the handle's close sentinel
            items.append(item if isinstance(item, str) else item[0])
    return items


def _events(db, sid: str, name: str) -> list[dict]:
    return [
        m["content"]
        for m in db.list_studio_chat_messages(sid)
        if m["kind"] == "status" and m["content"].get("event") == name
    ]


def test_resume_on_idle_replays_the_empty_turn_message_once(admission) -> None:
    service, db, sid, workspace, runtime = admission
    message = service.send_message(sid, workspace, "lost question")
    [original_prompt] = _drain(runtime)
    _empty_turn(service, sid, runtime)
    [notice] = _events(db, sid, "empty_turn")
    assert notice["message_id"] == message["id"]
    assert "继续对话" in notice["detail"]
    assert db.get_studio_chat_session(sid)["status"] == "idle"

    resumed = service.resume_session(sid, workspace, "unused-user")
    assert resumed["status"] == "running"
    # The exact prompt (bootstrap/transcript included) goes out again; no
    # second user bubble is written, a status row records the replay.
    assert _drain(runtime) == [original_prompt]
    assert db.count_studio_chat_user_messages(sid) == 1
    [retry] = _events(db, sid, "empty_turn_retry")
    assert retry["message_id"] == message["id"]
    assert runtime.empty_turn_retry is None
    assert runtime.turn_retry_source == (message["id"], "lost question", original_prompt)

    # Double click while the replay runs: plain no-op on a busy session.
    assert service.resume_session(sid, workspace, "unused-user")["status"] == "running"
    assert _drain(runtime) == []


def test_replay_slot_is_single_use_even_if_the_replay_is_empty_again(admission) -> None:
    service, db, sid, workspace, runtime = admission
    service.send_message(sid, workspace, "lost question")
    _empty_turn(service, sid, runtime)
    service.resume_session(sid, workspace, "unused-user")
    _drain(runtime)
    # The replay is itself confirmed empty: it may be replayed again (one
    # click = one delivery), never twice for the same verdict.
    _empty_turn(service, sid, runtime)
    assert len(_events(db, sid, "empty_turn")) == 2
    service.resume_session(sid, workspace, "unused-user")
    assert len(_drain(runtime)) == 1
    service._on_update(
        sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}}
    )
    service._on_turn_end(sid, "end_turn")
    assert service.resume_session(sid, workspace, "unused-user")["status"] == "idle"
    assert _drain(runtime) == []


def test_newer_message_supersedes_the_replay(admission) -> None:
    service, db, sid, workspace, runtime = admission
    service.send_message(sid, workspace, "lost question")
    _empty_turn(service, sid, runtime)
    service.send_message(sid, workspace, "resent by hand")
    _drain(runtime)
    service._on_update(
        sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}}
    )
    service._on_turn_end(sid, "end_turn")
    assert runtime.empty_turn_retry is None
    assert service.resume_session(sid, workspace, "unused-user")["status"] == "idle"
    assert _drain(runtime) == []
    assert _events(db, sid, "empty_turn_retry") == []


def _expires_at(db, runtime):
    with db.connect() as conn:
        row = conn.execute(
            "select expires_at from auth_scoped_tokens where token_hash=%s",
            (hash_token(runtime.token),),
        ).fetchone()
    value = row["expires_at"]
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def test_replay_renews_a_token_close_to_ttl(admission) -> None:
    """Human-admission parity: a click near the run token's TTL slides it
    forward before the replayed turn starts (codex R1 on #1028)."""
    service, db, sid, workspace, runtime = admission
    service.send_message(sid, workspace, "lost question")
    _drain(runtime)
    _empty_turn(service, sid, runtime)
    soon = datetime.now(UTC) + timedelta(minutes=2)
    with db.connect() as conn:
        conn.execute(
            "update auth_scoped_tokens set expires_at=%s where token_hash=%s",
            (soon, hash_token(runtime.token)),
        )
    assert service.resume_session(sid, workspace, "unused-user")["status"] == "running"
    assert _expires_at(db, runtime) > soon + timedelta(minutes=30)
    assert len(_drain(runtime)) == 1


def test_token_revoked_before_the_claim_sends_nothing(admission, monkeypatch) -> None:
    """The claim transaction re-checks the token under lock: a revoke landing
    after the pre-check refuses the replay with no turn claimed."""
    service, db, sid, workspace, runtime = admission
    service.send_message(sid, workspace, "lost question")
    _drain(runtime)
    _empty_turn(service, sid, runtime)
    real_claim = db.claim_studio_chat_turn_with_token

    def revoke_then_claim(session_id, token_hash, notice):
        db.revoke_scoped_token(token_hash)
        return real_claim(session_id, token_hash, notice)

    monkeypatch.setattr(db, "claim_studio_chat_turn_with_token", revoke_then_claim)
    with pytest.raises(ConflictError):
        service.resume_session(sid, workspace, "unused-user")
    assert _drain(runtime) == []
    assert db.get_studio_chat_session(sid)["status"] != "running"
    assert _events(db, sid, "empty_turn_retry") == []


def test_resume_on_idle_without_empty_turn_stays_a_no_op(admission) -> None:
    service, db, sid, workspace, runtime = admission
    assert service.resume_session(sid, workspace, "unused-user")["status"] == "idle"
    assert _drain(runtime) == []
    assert _events(db, sid, "empty_turn_retry") == []
