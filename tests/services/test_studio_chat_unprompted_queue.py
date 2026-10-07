"""Human messages sent during a Kimi Code unprompted turn are held, not lost (#1029).

Kimi Code 0.43 queues a ``session/prompt`` that arrives while its own
(unprompted) turn runs and settles it at once with zero content; the queued
input then runs unseen. Studio therefore holds the message until the wire
journal shows the unprompted turn ended, then hands it to the ACP prompt queue
with #1028's delivery. The watcher is driven step by step against a real
journal file and the real admission path (unstarted ACP handle).
"""

from __future__ import annotations

import json

import pytest

from server.app.services.job_errors import ConflictError
from server.app.studio_chat import inbound_queue, unprompted_queue
from server.app.studio_chat.background_wakeup import wake_session
from server.app.studio_chat.empty_turn_retry import retry_empty_turn
from server.app.studio_chat.unprompted_queue import GatedUnpromptedWatcher
from tests.helpers import studio_chat_fixtures

admission = studio_chat_fixtures.admission

PROMPT = {
    "type": "turn.prompt",
    "agentId": "main",
    "origin": {"kind": "task", "taskId": "agent-x1", "status": "completed"},
    "turnId": 3,
}
REPORT = {
    "type": "context.append_loop_event",
    "agentId": "main",
    "event": {"type": "content.part", "turnId": "3", "part": {"type": "text", "text": "汇报"}},
}
ENDED = {"type": "turn.ended", "agentId": "main", "turnId": 3, "reason": "completed"}


@pytest.fixture
def gated(admission, tmp_path):
    service, db, sid, workspace, runtime = admission
    path = tmp_path / "wire.jsonl"
    path.write_text("")
    watcher = GatedUnpromptedWatcher(service, sid, runtime, lambda: path)

    def write(*records):
        with path.open("a") as handle:
            handle.writelines(json.dumps(record) + "\n" for record in records)

    return service, db, sid, workspace, runtime, watcher, write, path


def _queue_items(runtime) -> list[tuple]:
    items = []
    while not runtime.handle._queue.empty():
        items.append(runtime.handle._queue.get_nowait())
    return items


def _events(db, sid: str, name: str) -> list[dict]:
    return [
        m["content"]
        for m in db.list_studio_chat_messages(sid)
        if m["kind"] == "status" and m["content"].get("event") == name
    ]


def _finish_turn(service, sid: str) -> None:
    chunk = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "ok"}}
    service._on_update(sid, chunk)
    service._on_turn_end(sid, "end_turn")


def test_messages_are_held_until_the_unprompted_turn_ends(gated) -> None:
    service, db, sid, workspace, runtime, watcher, write, _path = gated
    write(PROMPT, REPORT)
    watcher.step()
    assert watcher.open == {"3"}

    first = service.send_message(sid, workspace, "first")
    second = service.send_message(sid, workspace, "second")
    assert first["content"] == {"text": "first", "queued": True}
    assert second["content"]["queued"] is True
    # Nothing reached ACP and nothing was claimed: the engine would have
    # swallowed it behind its own turn.
    assert _queue_items(runtime) == []
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert runtime.inbound_pending == 2
    # Automatic turns stand back while the unprompted turn runs.
    assert not wake_session(service, sid, runtime, ["task"])

    write(ENDED)
    watcher.step()
    assert watcher.open == frozenset() and watcher.held == []
    (first_prompt, first_start), (second_prompt, second_start) = _queue_items(runtime)
    assert first_prompt.endswith("first") and second_prompt == "second"

    assert first_start()
    assert db.get_studio_chat_session(sid)["status"] == "running"
    assert runtime.turn_retry_source[0] == first["id"]
    _finish_turn(service, sid)
    assert second_start()
    _finish_turn(service, sid)
    assert runtime.inbound_pending == 0
    delivered = _events(db, sid, "queued_delivered")
    assert [event["message_id"] for event in delivered] == [first["id"], second["id"]]


def test_send_right_after_turn_end_stays_behind_flushed_messages(gated) -> None:
    service, db, sid, workspace, runtime, watcher, write, _path = gated
    write(PROMPT)
    watcher.step()
    held = service.send_message(sid, workspace, "held")
    write(ENDED)
    watcher.step()
    later = service.send_message(sid, workspace, "later")
    # The held message is in the ACP queue but has not started: the later
    # one queues behind it (#1028 FIFO) instead of claiming directly.
    assert later["content"]["queued"] is True
    (_p1, start_held), (_p2, start_later) = _queue_items(runtime)
    assert start_held()
    assert runtime.turn_retry_source[0] == held["id"]
    _finish_turn(service, sid)
    assert start_later()
    assert runtime.turn_retry_source[0] == later["id"]


def test_admission_observes_a_turn_the_poll_has_not_seen(gated) -> None:
    """The watcher polls once a second; admission steps it first, so a turn
    already in the journal gates the send."""
    service, _db, sid, workspace, runtime, watcher, write, _path = gated
    watcher.step()
    write(PROMPT)
    message = service.send_message(sid, workspace, "racing")
    assert message["content"]["queued"] is True
    assert watcher.held and watcher.held[0][0] == message["id"]
    assert _queue_items(runtime) == []


def test_turn_end_not_yet_durable_keeps_holding(gated, monkeypatch) -> None:
    service, db, sid, workspace, runtime, watcher, write, _path = gated
    write(PROMPT)
    watcher.step()
    service.send_message(sid, workspace, "wait")
    append = service.store.append_message

    def fail_turn_end(session_id, kind, role, content):
        if content.get("event") == "turn_end":
            raise RuntimeError("db down")
        return append(session_id, kind, role, content)

    write(ENDED)
    with monkeypatch.context() as patch:
        patch.setattr(service.store, "append_message", fail_turn_end)
        with pytest.raises(RuntimeError):
            watcher.step()
    # The projector saw turn.ended, but its row is not durable yet.
    assert watcher.open == {"3"} and len(watcher.held) == 1
    assert _queue_items(runtime) == []
    watcher.step()
    assert watcher.open == frozenset()
    assert len(_queue_items(runtime)) == 1


def test_turn_whose_receipt_write_failed_still_holds(gated, monkeypatch) -> None:
    """PR #1076 review P2: the first record of a turn was projected but its
    receipt append failed (pending backlog). The gate opens on what the
    projector saw — admission's refresh swallows the retry failure — so the
    message is held instead of reaching ACP and being swallowed."""
    service, db, sid, workspace, runtime, watcher, write, _path = gated
    append = service.store.append_message

    def fail_receipt(session_id, kind, role, content):
        if content.get("event") == "unprompted_turn":
            raise RuntimeError("db blip")
        return append(session_id, kind, role, content)

    write(PROMPT)
    with monkeypatch.context() as patch:
        patch.setattr(service.store, "append_message", fail_receipt)
        with pytest.raises(RuntimeError):
            watcher.step()
        assert watcher.pending and watcher.open == {"3"}
        message = service.send_message(sid, workspace, "during blip")
    assert message["content"]["queued"] is True
    assert watcher.held[0][0] == message["id"]
    assert _queue_items(runtime) == []
    assert db.get_studio_chat_session(sid)["status"] == "idle"

    write(ENDED)
    watcher.step()  # backlog persisted, then the end: released in order
    assert watcher.open == frozenset() and watcher.held == []
    [(_prompt, start)] = _queue_items(runtime)
    assert start()


def test_truncated_journal_releases_the_gate_and_drops_held(gated) -> None:
    service, db, sid, workspace, runtime, watcher, write, path = gated
    write(PROMPT, REPORT)
    watcher.step()
    held = service.send_message(sid, workspace, "stuck")
    path.write_text("")  # truncated: turn.ended can no longer be observed
    watcher.step()
    assert watcher.open == frozenset() and watcher.held == []
    assert runtime.inbound_pending == 0
    [dropped] = _events(db, sid, "queued_dropped")
    assert dropped == {
        "event": "queued_dropped",
        "message_id": held["id"],
        "detail": unprompted_queue.DROPPED_STALLED,
    }
    assert _queue_items(runtime) == []
    # The gate is released: the next message is an ordinary turn again.
    direct = service.send_message(sid, workspace, "again")
    assert "queued" not in direct["content"]
    assert db.get_studio_chat_session(sid)["status"] == "running"


def test_stalled_turn_times_out_even_when_steps_keep_failing(gated, monkeypatch) -> None:
    service, db, sid, workspace, runtime, watcher, write, _path = gated
    write(PROMPT)
    watcher.step()
    held = service.send_message(sid, workspace, "stalled")

    def broken_read():
        raise OSError("journal unreadable")

    monkeypatch.setattr(watcher.tail, "read", broken_read)
    with pytest.raises(OSError):
        watcher.step()
    assert len(watcher.held) == 1  # not yet stale
    monkeypatch.setattr(unprompted_queue, "IDLE_TIMEOUT_SECONDS", 0.0)
    with pytest.raises(OSError):
        watcher.step()
    assert watcher.open == frozenset() and watcher.held == []
    assert _events(db, sid, "queued_dropped")[0]["message_id"] == held["id"]
    # The expired turn stays out of the gate until the projector forgets it.
    assert watcher.expired == {"3"}


def test_empty_turn_replay_is_refused_while_held(gated) -> None:
    service, _db, sid, _workspace, runtime, watcher, write, _path = gated
    write(PROMPT)
    watcher.step()
    runtime.empty_turn_retry = ("msg", "text", "prompt")
    with pytest.raises(ConflictError, match="自发"):
        retry_empty_turn(service, sid, runtime)
    assert runtime.empty_turn_retry == ("msg", "text", "prompt")
    assert _queue_items(runtime) == []


def test_empty_turn_replay_refreshes_the_gate_before_deciding(gated) -> None:
    service, _db, sid, _workspace, runtime, watcher, write, _path = gated
    # The unprompted turn is on the wire but the watcher has not polled yet:
    # the replay must step it first (as send_message does), not slip into it.
    write(PROMPT)
    assert watcher.open == frozenset()
    runtime.empty_turn_retry = ("msg", "text", "prompt")
    with pytest.raises(ConflictError, match="自发"):
        retry_empty_turn(service, sid, runtime)
    assert watcher.open == {"3"}
    assert runtime.empty_turn_retry == ("msg", "text", "prompt")
    assert _queue_items(runtime) == []


def test_closed_runtime_never_flushes(gated) -> None:
    service, _db, sid, workspace, runtime, watcher, write, _path = gated
    write(PROMPT)
    watcher.step()
    service.send_message(sid, workspace, "orphan")
    with runtime.lock:
        runtime.closed = True
    write(ENDED)
    watcher.step()
    assert _queue_items(runtime) == []


def test_runtime_without_gate_is_unchanged(admission) -> None:
    service, _db, sid, workspace, runtime = admission
    assert runtime.unprompted_gate is None
    assert not unprompted_queue.should_hold(runtime, "idle")
    message = service.send_message(sid, workspace, "plain")
    assert "queued" not in message["content"]
    assert inbound_queue.should_queue(runtime, "running") is False
