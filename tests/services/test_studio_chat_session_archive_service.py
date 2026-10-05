"""Studio chat session archive at the service layer (#924).

Runtime semantics match close: archiving a live session closes it and
revokes the run token; an archived session cannot be resumed (409) until it
is unarchived, and unarchive never spawns a runtime. An archive that lands
between a resume's claim and its runtime registration must be caught by the
spawn registration fence (the #872 delete fence, extended to archived_at).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from server.app.auth.scoped_tokens import authenticate_scoped_token
from server.app.services.job_errors import ConflictError, InvalidOperationError
from server.app.studio_chat import resume as resume_module
from server.app.studio_chat import session_settle as settle_module
from server.app.studio_chat import spawn as spawn_module
from tests.helpers import studio_chat_fixtures

TEXT_SCRIPT = studio_chat_fixtures.TEXT_SCRIPT
chat = studio_chat_fixtures.chat


def _session_new_count(script_path: Path) -> int:
    sink = Path(str(script_path) + ".sink.jsonl")
    if not sink.exists():
        return 0
    return sum(
        1
        for line in sink.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("received", {}).get("method") == "session/new"
    )


def _capture_mints(monkeypatch) -> list[str]:
    minted: list[str] = []
    original_mint = spawn_module.mint_scoped_token

    def capture_mint(*args, **kwargs):
        token = original_mint(*args, **kwargs)
        minted.append(token)
        return token

    monkeypatch.setattr(spawn_module, "mint_scoped_token", capture_mint)
    return minted


def test_archive_live_session_closes_runtime_and_blocks_resume(chat, job_db, monkeypatch) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    minted = _capture_mints(monkeypatch)
    session_id = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    assert service.runtime(session_id) is not None

    archived = service.archive_session(session_id, workspace_id)
    assert archived["status"] == "closed"
    assert archived["archived_at"] is not None
    assert service.runtime(session_id) is None
    assert authenticate_scoped_token(job_db, minted[0]) is None
    # Idempotent: archiving again answers the same row.
    assert service.archive_session(session_id, workspace_id)["archived_at"] is not None

    spawned = _session_new_count(script_path)
    with pytest.raises(ConflictError, match="archived"):
        service.resume_session(session_id, workspace_id, user_id)
    assert _session_new_count(script_path) == spawned

    restored = service.unarchive_session(session_id, workspace_id)
    assert restored["archived_at"] is None
    # Unarchive never spawns: the row stays closed with no runtime ...
    assert restored["status"] == "closed"
    assert service.runtime(session_id) is None
    assert _session_new_count(script_path) == spawned
    # ... and the existing resume path brings it back.
    assert service.resume_session(session_id, workspace_id, user_id)["status"] == "idle"
    service.close_session(session_id, workspace_id)


def test_archive_between_resume_claim_and_registration_leaves_no_runtime(
    chat, job_db, monkeypatch
) -> None:
    service, _bus, register, workspace_id, user_id = chat
    script_path = register(TEXT_SCRIPT)
    session_id = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    service.close_session(session_id, workspace_id)
    spawned_before = _session_new_count(script_path)
    minted = _capture_mints(monkeypatch)
    original_spawn = resume_module.spawn_session_runtime

    def archive_then_spawn(*args, **kwargs):
        # The resume already claimed (closed -> starting); the archive lands
        # before the runtime registers, finds nothing to retire, and closes.
        service.archive_session(session_id, workspace_id)
        assert service.runtime(session_id) is None
        return original_spawn(*args, **kwargs)

    monkeypatch.setattr(resume_module, "spawn_session_runtime", archive_then_spawn)

    with pytest.raises(InvalidOperationError, match="archived"):
        service.resume_session(session_id, workspace_id, user_id)

    assert service.runtime(session_id) is None
    row = job_db.get_studio_chat_session(session_id)
    assert row is not None
    assert row["status"] == "closed"
    assert row["archived_at"] is not None
    assert len(minted) == 1
    assert authenticate_scoped_token(job_db, minted[0]) is None
    assert _session_new_count(script_path) == spawned_before


def test_unarchive_between_stamp_and_close_keeps_the_restored_session_live(
    chat, job_db, monkeypatch
) -> None:
    """#924 review P1: an unarchive landing after the archive stamp but before
    the close must win — the archive request abandons its close instead of
    shutting down the session the unarchive just restored, and both answers
    reflect the real (live, unarchived) state."""
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    minted = _capture_mints(monkeypatch)
    session_id = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    runtime = service.runtime(session_id)
    assert runtime is not None
    original_close = settle_module.close_session
    restored: list[dict] = []

    def unarchive_then_close(*args, **kwargs):
        if not restored:
            restored.append(service.unarchive_session(session_id, workspace_id))
        return original_close(*args, **kwargs)

    monkeypatch.setattr(settle_module, "close_session", unarchive_then_close)
    archived = service.archive_session(session_id, workspace_id)

    assert restored and restored[0]["archived_at"] is None
    assert restored[0]["status"] == "idle"
    assert archived["archived_at"] is None
    assert archived["status"] == "idle"
    assert service.runtime(session_id) is runtime
    assert authenticate_scoped_token(job_db, minted[0]) is not None
    service.close_session(session_id, workspace_id)


def test_archive_close_under_lock_rechecks_the_stamp(chat, job_db, monkeypatch) -> None:
    """The stamp is re-validated inside close_session's critical section too:
    an unarchive landing after the settle loop's pre-check (but before the
    closed write) still prevents the close."""
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    session_id = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    original_close = settle_module.close_session

    def close_with_late_unarchive(*args, still_wanted=None, **kwargs):
        def late_unarchive_then_check(row):
            # The unarchive commits just before the in-lock re-validation
            # (in production it holds _runtimes_lock, so this is the latest
            # point it can land); the check must re-read and see it.
            job_db.set_studio_chat_session_archived(session_id, False)
            return still_wanted(job_db.get_studio_chat_session(session_id))

        return original_close(*args, still_wanted=late_unarchive_then_check, **kwargs)

    monkeypatch.setattr(settle_module, "close_session", close_with_late_unarchive)
    archived = service.archive_session(session_id, workspace_id)

    assert archived["archived_at"] is None
    assert archived["status"] == "idle"
    assert service.runtime(session_id) is not None
    service.close_session(session_id, workspace_id)


def test_concurrent_archives_write_a_single_session_closed(chat, job_db, monkeypatch) -> None:
    """#940: two archives of the same live session that both passed close's
    pre-lock check serialize on runtime.lock; the later one must re-check the
    closed state under the lock and leave the terminal marker to the first."""
    service, _bus, register, workspace_id, user_id = chat
    register(TEXT_SCRIPT)
    minted = _capture_mints(monkeypatch)
    session_id = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    runtime = service.runtime(session_id)
    assert runtime is not None

    snapshotted: set[threading.Thread] = set()
    both_snapshotted = threading.Event()
    original_runtime = service.runtime

    def tracking_runtime(sid):
        if threading.current_thread() in archivers:
            snapshotted.add(threading.current_thread())
            if len(snapshotted) == len(archivers):
                both_snapshotted.set()
        return original_runtime(sid)

    monkeypatch.setattr(service, "runtime", tracking_runtime)
    results: list[dict] = []
    archivers = [
        threading.Thread(
            target=lambda: results.append(service.archive_session(session_id, workspace_id))
        )
        for _ in range(2)
    ]
    # Hold the generation lock so both archives pin the live runtime before
    # either can commit the closed write.
    with runtime.lock:
        for thread in archivers:
            thread.start()
        assert both_snapshotted.wait(10)
    for thread in archivers:
        thread.join(10)
        assert not thread.is_alive()

    assert len(results) == 2
    assert all(result["status"] == "closed" for result in results)
    assert original_runtime(session_id) is None
    assert authenticate_scoped_token(job_db, minted[0]) is None
    events = [
        message["content"].get("event")
        for message in service.list_messages(session_id, workspace_id)
        if message["kind"] == "status"
    ]
    assert events.count("session_closed") == 1
