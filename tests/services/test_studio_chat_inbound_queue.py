"""Human messages sent during a background wakeup turn are queued, not lost (#882).

Drives the real admission path against an unstarted ACP handle: its prompt
queue is inspected directly and each item's ``before_start`` is invoked the
way the prompt loop would, after the previous turn settled.
"""

from __future__ import annotations

import time

import pytest

from server.app.services.job_errors import ConflictError
from server.app.studio_chat import inbound_queue
from server.app.studio_chat.background_wakeup import wake_session
from tests.helpers import studio_chat_fixtures

admission = studio_chat_fixtures.admission


def _next_item(runtime):
    item = runtime.handle._queue.get_nowait()
    assert isinstance(item, tuple), item
    return item


def _events(db, sid: str, name: str) -> list[dict]:
    return [
        m["content"]
        for m in db.list_studio_chat_messages(sid)
        if m["kind"] == "status" and m["content"].get("event") == name
    ]


def _finish_turn(service, sid: str) -> None:
    """Settle a turn that produced content (no empty_turn verdict armed)."""
    chunk = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}}
    service._on_update(sid, chunk)
    service._on_turn_end(sid, "end_turn")


def _start_background_turn(service, db, sid, runtime):
    assert wake_session(service, sid, runtime, ["finished-task"])
    assert db.get_studio_chat_session(sid)["status"] == "running"
    _wake_text, wake_guard = _next_item(runtime)
    assert wake_guard()


def test_messages_during_background_turn_queue_and_deliver_in_order(admission) -> None:
    service, db, sid, workspace, runtime = admission
    _start_background_turn(service, db, sid, runtime)

    first = service.send_message(sid, workspace, "first")
    second = service.send_message(sid, workspace, "second")
    assert first["content"] == {"text": "first", "queued": True}
    assert second["content"]["queued"] is True
    # The background turn still owns the session; nothing was claimed.
    assert db.get_studio_chat_session(sid)["status"] == "running"
    assert runtime.inbound_pending == 2
    assert not runtime.resume_transcript_pending

    first_prompt, first_start = _next_item(runtime)
    second_prompt, second_start = _next_item(runtime)
    assert first_prompt.endswith("first")
    assert second_prompt == "second"

    _finish_turn(service, sid)
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    # Queued human input goes before any new automatic turn.
    assert not wake_session(service, sid, runtime, ["another-task"])

    assert first_start()
    assert db.get_studio_chat_session(sid)["status"] == "running"
    assert runtime.turn_retry_source == (first["id"], "first", first_prompt)
    assert not runtime.turn_background
    _finish_turn(service, sid)
    assert second_start()
    assert runtime.turn_retry_source[0] == second["id"]
    assert runtime.inbound_pending == 0
    delivered = _events(db, sid, "queued_delivered")
    assert [event["message_id"] for event in delivered] == [first["id"], second["id"]]


def test_send_after_background_turn_end_keeps_fifo_behind_queue(admission) -> None:
    service, db, sid, workspace, runtime = admission
    _start_background_turn(service, db, sid, runtime)
    queued = service.send_message(sid, workspace, "queued")
    _finish_turn(service, sid)
    # Idle, but a queued message has not started yet: a direct claim here
    # would overtake it, so the new message queues as well.
    later = service.send_message(sid, workspace, "later")
    assert later["content"]["queued"] is True
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    _prompt, start = _next_item(runtime)
    assert start()
    assert runtime.turn_retry_source[0] == queued["id"]


def test_queued_message_that_cannot_start_is_dropped_visibly(admission) -> None:
    service, db, sid, workspace, runtime = admission
    _start_background_turn(service, db, sid, runtime)
    queued = service.send_message(sid, workspace, "during compaction")
    _prompt, start = _next_item(runtime)
    _finish_turn(service, sid)
    with runtime.lock:
        runtime.compacting = True
        runtime.compacting_since = time.monotonic()

    assert not start()
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert runtime.inbound_pending == 0
    [dropped] = _events(db, sid, "queued_dropped")
    assert dropped == {
        "event": "queued_dropped",
        "message_id": queued["id"],
        "detail": inbound_queue.DROPPED_COMPACTING,
    }


def test_human_turn_still_refuses_a_concurrent_send(admission) -> None:
    service, db, sid, workspace, runtime = admission
    service.send_message(sid, workspace, "human turn")
    with pytest.raises(ConflictError, match="busy"):
        service.send_message(sid, workspace, "while running")
    assert db.count_studio_chat_user_messages(sid) == 1
    assert runtime.inbound_pending == 0
