"""Human messages sent during a Kimi Code unprompted turn are held, not lost (#1029).

Kimi Code 0.43 queues a ``session/prompt`` that arrives while its own
(unprompted) turn runs and settles it at once with zero content; the queued
input then runs unseen. Studio therefore holds the message until the wire
journal shows the unprompted turn ended, then hands it to the ACP prompt queue
with #1028's delivery. The watcher is driven step by step against a real
journal file and the real admission path (unstarted ACP handle).
"""

from __future__ import annotations

import contextlib
import json
import threading

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
    watcher.step()  # #1044: the backlog is persisted before the journal is read
    assert watcher.open == {"3"} and watcher.held
    watcher.step()  # then the end is read: held messages released in order
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


# --- #1109: every dequeue re-observes the journal -----------------------------
#
# Decision table for a queued message's ``before_start`` (GatedUnpromptedWatcher
# .defer), and for the wake_session send boundary. Driven synchronously: the
# real prompt loop calls these on a worker thread (prompt_turn.run_prompt_turn).


def _turn(turn_id: int) -> tuple[dict, dict]:
    prompt = {**PROMPT, "turnId": turn_id, "origin": {"kind": "cron_job"}}
    return prompt, {**ENDED, "turnId": turn_id}


def _held_ids(watcher) -> list[str]:
    return [message_id for message_id, _text, _prompt in watcher.held]


@contextlib.contextmanager
def _step_in_progress(watcher, active: bool):
    """While active, another thread holds the step lock (a concurrent step
    that outlives the dequeue's wait). Signal-driven, no sleeps."""
    if not active:
        yield
        return
    taken, done = threading.Event(), threading.Event()

    def concurrent_step() -> None:
        with watcher.step_lock:
            taken.set()
            done.wait()

    thread = threading.Thread(target=concurrent_step)
    thread.start()
    assert taken.wait(timeout=30)
    try:
        yield
    finally:
        done.set()
        thread.join(timeout=30)
        assert not thread.is_alive()


def _flushed(service, sid, workspace, watcher, write, *texts) -> list[dict]:
    """Hold ``texts`` behind an unprompted turn, end it, and step: all of them
    now sit in the ACP queue (not yet started)."""
    write(PROMPT)
    watcher.step()
    messages = [service.send_message(sid, workspace, text) for text in texts]
    write(ENDED)
    watcher.step()
    return messages


def test_unprompted_turn_between_two_flushed_messages_holds_the_second(gated) -> None:
    """Issue #1109 acceptance: Kimi opens a turn (task / cron) right after the
    first flushed message ends and before the second starts — no poll in
    between. The second stays held and is delivered after that turn ends."""
    service, db, sid, workspace, runtime, watcher, write, _path = gated
    first, second = _flushed(service, sid, workspace, watcher, write, "first", "second")
    (_p1, start_first), (_p2, start_second) = _queue_items(runtime)
    assert start_first()
    _finish_turn(service, sid)

    opened, ended = _turn(4)
    write(opened)  # on the journal, not yet polled
    assert not start_second()
    assert watcher.open == {"4"} and _held_ids(watcher) == [second["id"]]
    assert runtime.inbound_pending == 1  # still pending, not dropped
    assert _queue_items(runtime) == []
    assert db.get_studio_chat_session(sid)["status"] == "idle"
    assert _events(db, sid, "queued_dropped") == []

    write(ended)
    watcher.step()
    [(_p, start_again)] = _queue_items(runtime)
    assert start_again()
    _finish_turn(service, sid)
    assert runtime.inbound_pending == 0
    delivered = _events(db, sid, "queued_delivered")
    assert [event["message_id"] for event in delivered] == [first["id"], second["id"]]


@pytest.mark.parametrize(
    ("case", "sent", "put_back"),
    [
        ("journal_idle", True, False),  # observed, gate closed, nothing put back
        ("turn_opened", False, True),  # observed, a new unprompted turn → back to held
        ("step_busy", False, True),  # concurrent step outlived the wait → back, unconfirmed
        ("step_failed", True, False),  # broken read: decide on the settled state
        ("runtime_closed", False, False),  # torn down: #1028 drop semantics, not held
    ],
)
def test_dequeue_decision_table(gated, monkeypatch, case, sent, put_back) -> None:
    service, db, sid, workspace, runtime, watcher, write, _path = gated
    [message] = _flushed(service, sid, workspace, watcher, write, "only")
    [(_prompt, start)] = _queue_items(runtime)
    if case == "turn_opened":
        write(_turn(4)[0])
    elif case == "step_busy":
        monkeypatch.setattr(unprompted_queue, "DEQUEUE_STEP_TIMEOUT_SECONDS", 0)
    elif case == "step_failed":

        def broken_read():
            raise OSError("journal unreadable")

        monkeypatch.setattr(watcher.tail, "read", broken_read)
    elif case == "runtime_closed":
        with runtime.lock:
            runtime.closed = True
    with _step_in_progress(watcher, case == "step_busy"):
        assert start() is sent
    assert _held_ids(watcher) == ([message["id"]] if put_back else [])
    # inbound_pending == held + still-queued: a put-back message stays counted.
    assert runtime.inbound_pending == (1 if put_back else 0)
    assert db.get_studio_chat_session(sid)["status"] == ("running" if sent else "idle")
    # An unconfirmed put-back is not flushed straight back (no bounce); the
    # next settled step releases it once the gate is closed.
    assert _queue_items(runtime) == []
    if case == "step_busy":
        watcher.step()
        [(_p, start_again)] = _queue_items(runtime)
        assert start_again()


def test_put_back_keeps_fifo_with_messages_arriving_meanwhile(gated) -> None:
    """A, B flushed; C queued behind them (#1028); a turn opens before B. B
    goes back to held, D arrives (held), C comes back between B and D."""
    service, db, sid, workspace, runtime, watcher, write, _path = gated
    a, b = _flushed(service, sid, workspace, watcher, write, "a", "b")
    c = service.send_message(sid, workspace, "c")
    (_pa, start_a), (_pb, start_b), (_pc, start_c) = _queue_items(runtime)
    assert start_a()
    _finish_turn(service, sid)
    opened, ended = _turn(4)
    write(opened)
    assert not start_b()
    d = service.send_message(sid, workspace, "d")
    assert d["content"]["queued"] is True and _queue_items(runtime) == []
    assert not start_c()
    assert _held_ids(watcher) == [b["id"], c["id"], d["id"]]
    assert runtime.inbound_pending == 3

    write(ended)
    watcher.step()
    starts = [start for _prompt, start in _queue_items(runtime)]
    for start in starts:
        assert start()
        _finish_turn(service, sid)
    delivered = [event["message_id"] for event in _events(db, sid, "queued_delivered")]
    assert delivered == [a["id"], b["id"], c["id"], d["id"]]
    assert runtime.inbound_pending == 0 and watcher.held == [] and watcher.front == 0


def test_release_waits_for_queued_messages_and_never_reorders(gated) -> None:
    """A goes back to held while the turn is open; the turn ends before B's
    dequeue. Releasing A at that settle would land it behind B: instead B
    (younger) goes back behind A and both are released in order."""
    service, db, sid, workspace, runtime, watcher, write, _path = gated
    a, b = _flushed(service, sid, workspace, watcher, write, "a", "b")
    (_pa, start_a), (_pb, start_b) = _queue_items(runtime)
    opened, ended = _turn(4)
    write(opened)
    assert not start_a()
    write(ended)
    watcher.step()  # gate closed, but B is still in the ACP queue
    assert _held_ids(watcher) == [a["id"]] and _queue_items(runtime) == []
    assert not start_b()
    starts = [start for _prompt, start in _queue_items(runtime)]
    assert len(starts) == 2 and watcher.held == []
    for start in starts:
        assert start()
        _finish_turn(service, sid)
    delivered = [event["message_id"] for event in _events(db, sid, "queued_delivered")]
    assert delivered == [a["id"], b["id"]]


@pytest.mark.parametrize(
    ("case", "sent"),
    [("journal_idle", True), ("turn_opened", False), ("step_busy", False)],
)
def test_wakeup_send_boundary_observes_the_journal(gated, monkeypatch, case, sent) -> None:
    """wake_session's claim reads the last poll (its caller holds runtime.lock);
    the send boundary steps the watcher and stands back from an open turn,
    re-arming the task ids instead of burning the run token."""
    from types import SimpleNamespace

    from server.app.studio_chat import background_delivery

    service, db, sid, _workspace, runtime, watcher, write, _path = gated
    watcher.step()
    runtime.background_cursor = SimpleNamespace(pending=set())
    invalidate = []
    monkeypatch.setattr(
        background_delivery, "invalidate_run_token", lambda *args: invalidate.append(args)
    )
    assert wake_session(service, sid, runtime, ["agent-1"])
    [(_prompt, start)] = _queue_items(runtime)
    if case == "turn_opened":
        write(PROMPT)
    elif case == "step_busy":
        monkeypatch.setattr(background_delivery, "DEQUEUE_STEP_TIMEOUT_SECONDS", 0)
    with _step_in_progress(watcher, case == "step_busy"):
        assert start() is sent
    assert invalidate == []
    status = db.get_studio_chat_session(sid)["status"]
    assert status == ("running" if sent else "idle")
    assert runtime.turn_open is sent
    assert runtime.background_cursor.pending == (set() if sent else {"agent-1"})


def test_dequeue_guard_runs_off_the_acp_event_loop() -> None:
    """The guard may step the watcher (journal read + DB writes) and take
    runtime.lock: it must not run on the ACP event loop thread."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from server.app.studio_chat.prompt_turn import run_prompt_turn

    threads: dict[str, int] = {}

    def guard() -> bool:
        threads["guard"] = threading.get_ident()
        return True

    conn = SimpleNamespace(prompt=AsyncMock(return_value=SimpleNamespace(stop_reason="end_turn")))

    async def dispatch():
        threads["loop"] = threading.get_ident()
        return await run_prompt_turn(conn, "acp-1", "hi", on_timeout=Mock(), before_start=guard)

    result = asyncio.run(dispatch())
    assert result.response is not None and conn.prompt.await_count == 1
    assert threads["guard"] != threads["loop"]
