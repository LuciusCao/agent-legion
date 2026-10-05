"""Orphaned Studio chat sessions after a backend restart (#760).

The DB row is the durable status, the runtime registry is process-local. The
startup reap repairs rows a dead process left live (skipped when another
replica may own them), and a send to a row with no runtime projects it to
error with a structured, resume-pointing 409 instead of a bare string.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_errors import ConflictError
from server.app.studio_chat.session_orphan import (
    ORPHAN_ERROR_DETAIL,
    SESSION_INTERRUPTED_CODE,
    StudioChatSessionInterruptedError,
)
from tests.helpers import studio_chat_fixtures, wait_for_predicate

chat = studio_chat_fixtures.chat


def _orphan(job_db, workspace_id: str, user_id: str, status: str = "idle") -> str:
    session_id = job_db.create_studio_chat_session(workspace_id, user_id, "fake-agent")
    job_db.update_studio_chat_session(session_id, status=status)
    return session_id


def _session_publishes(bus, session_id: str) -> list[dict]:
    return [
        payload["session"]
        for channel, payload in bus.events
        if session_id in channel and payload.get("type") == "session"
    ]


@pytest.mark.parametrize("status", ["idle", "running", "awaiting_permission"])
def test_send_to_orphan_projects_error_and_answers_structured_409(chat, job_db, status) -> None:
    service, bus, _register, workspace_id, user_id = chat
    sid = _orphan(job_db, workspace_id, user_id, status)

    with pytest.raises(StudioChatSessionInterruptedError) as caught:
        service.send_message(sid, workspace_id, "hello after restart")

    assert caught.value.payload["code"] == SESSION_INTERRUPTED_CODE
    assert caught.value.payload["session_id"] == sid
    assert "resume" in caught.value.payload["message"]
    row = job_db.get_studio_chat_session(sid)
    assert row["status"] == "error"
    assert row["error_detail"] == ORPHAN_ERROR_DETAIL
    # The refused text never becomes conversation history.
    assert job_db.count_studio_chat_user_messages(sid) == 0
    events = [m["content"] for m in job_db.list_studio_chat_messages(sid) if m["kind"] == "status"]
    assert {"event": "error", "detail": ORPHAN_ERROR_DETAIL} in events
    # SSE subscribers see the error snapshot -> the resume bar renders.
    assert _session_publishes(bus, sid)[-1]["status"] == "error"


def test_interrupted_error_maps_to_409_with_payload_detail() -> None:
    with pytest.raises(HTTPException) as caught:
        raise_job_http_error(StudioChatSessionInterruptedError("sid-1"))
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == SESSION_INTERRUPTED_CODE
    assert caught.value.detail["message"]


def test_send_to_already_errored_session_does_not_restamp(chat, job_db) -> None:
    service, _bus, _register, workspace_id, user_id = chat
    sid = _orphan(job_db, workspace_id, user_id, "error")
    job_db.update_studio_chat_session(sid, error_detail="agent process exited")

    with pytest.raises(StudioChatSessionInterruptedError):
        service.send_message(sid, workspace_id, "hi")

    assert job_db.get_studio_chat_session(sid)["error_detail"] == "agent process exited"
    assert job_db.list_studio_chat_messages(sid) == []


def test_closed_and_starting_sessions_keep_their_refusals(chat, job_db) -> None:
    service, _bus, _register, workspace_id, user_id = chat
    closed = _orphan(job_db, workspace_id, user_id, "closed")
    starting = _orphan(job_db, workspace_id, user_id, "starting")

    with pytest.raises(ConflictError, match="closed"):
        service.send_message(closed, workspace_id, "hi")
    # starting = create/resume still registering the runtime: not an orphan.
    with pytest.raises(ConflictError) as caught:
        service.send_message(starting, workspace_id, "hi")
    assert not isinstance(caught.value, StudioChatSessionInterruptedError)

    assert job_db.get_studio_chat_session(closed)["status"] == "closed"
    assert job_db.get_studio_chat_session(starting)["status"] == "starting"


def test_mode_switch_on_orphan_uses_the_same_projection(chat, job_db) -> None:
    """Session mode/config switches need the live runtime too: an orphan
    answers the same structured refusal and lands error."""
    from server.app.studio_chat.session_config import set_session_mode

    service, _bus, _register, workspace_id, user_id = chat
    sid = _orphan(job_db, workspace_id, user_id)
    job_db.update_studio_chat_session(
        sid, session_modes={"currentModeId": "a", "availableModes": [{"id": "a"}, {"id": "b"}]}
    )

    with pytest.raises(StudioChatSessionInterruptedError):
        set_session_mode(service, sid, workspace_id, "b")
    assert job_db.get_studio_chat_session(sid)["status"] == "error"


def test_runtime_registered_during_projection_rolls_the_stamp_back(
    chat, job_db, monkeypatch
) -> None:
    """A resume registering a runtime between the absence check and the
    error write must not leave its live session stamped error."""
    service, bus, _register, workspace_id, user_id = chat
    sid = _orphan(job_db, workspace_id, user_id, "running")
    calls = iter([None, object()])  # admission sees none; post-write recheck sees one
    monkeypatch.setattr(service, "runtime", lambda session_id: next(calls))

    with pytest.raises(ConflictError) as caught:
        service.send_message(sid, workspace_id, "hi")

    assert not isinstance(caught.value, StudioChatSessionInterruptedError)
    row = job_db.get_studio_chat_session(sid)
    assert row["status"] == "running"
    assert row["error_detail"] == ""
    assert job_db.list_studio_chat_messages(sid) == []
    assert _session_publishes(bus, sid) == []


def test_orphan_projected_by_send_is_resumable(chat, job_db) -> None:
    """End to end: runtime lost while the row says idle -> the send lands
    error, resume rebuilds the runtime, and the next send goes through."""
    service, _bus, register, workspace_id, user_id = chat
    register(studio_chat_fixtures.TEXT_SCRIPT)
    sid = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    # Drop the runtime without any status write (what a restart leaves).
    service.teardown_runtime(sid, service.runtime(sid))
    assert job_db.get_studio_chat_session(sid)["status"] == "idle"

    with pytest.raises(StudioChatSessionInterruptedError):
        service.send_message(sid, workspace_id, "lost")
    assert service.get_session(sid)["status"] == "error"

    assert service.resume_session(sid, workspace_id, user_id)["status"] == "idle"
    service.send_message(sid, workspace_id, "back")
    wait_for_predicate(
        lambda: (
            service.get_session(sid)["status"] == "idle"
            and job_db.count_studio_chat_user_messages(sid) == 1
        ),
        timeout=20.0,
        interval=0.05,
    )


class _FakeProbe:
    """SingleReplicaProbe stand-in with a fixed probe verdict."""

    verdict: bool | None = None

    def __init__(self, db_source) -> None:
        del db_source

    @property
    def lock_acquired(self) -> bool | None:
        return type(self).verdict

    def probe(self) -> bool:
        return self.verdict is not False

    def close(self) -> None:
        return None


@pytest.mark.parametrize("verdict", [True, None, False])
def test_lifespan_reap_runs_whatever_the_single_replica_probe_says(
    tmp_path, monkeypatch, job_db, verdict
) -> None:
    """The reap is unconditional (#760): False is also what a restart that
    overlaps the still-exiting old process sees (dev-down only waited for
    the port), and skipping there would strand the orphans — idle, no
    resume bar, resume refused — until the next restart."""
    from fastapi.testclient import TestClient

    from server.app import main
    from tests.helpers import setup_spa_app

    _root, data_dir = setup_spa_app(tmp_path, monkeypatch)
    workspace_id = job_db.create_workspace(name="Reap WS")["id"]
    user_id = str(job_db.create_user("reap-user", password_hash=None)["id"])
    sid = _orphan(job_db, workspace_id, user_id)
    monkeypatch.setattr(_FakeProbe, "verdict", verdict)
    monkeypatch.setattr(main, "SingleReplicaProbe", _FakeProbe)

    with TestClient(main.create_app(data_dir=data_dir, start_worker=False)):
        assert job_db.get_studio_chat_session(sid)["status"] == "error"


def test_restart_overlap_orphan_stays_resumable(chat, job_db) -> None:
    """Restart-overlap shape end to end: the old process's runtime is gone,
    the new process's probe found the old lock still held — the reap still
    lands the row on error, so resume works without a wasted send."""
    service, _bus, register, workspace_id, user_id = chat
    register(studio_chat_fixtures.TEXT_SCRIPT)
    sid = service.create_session(workspace_id, user_id, "fake-agent")["id"]
    service.teardown_runtime(sid, service.runtime(sid))  # old process gone, row idle

    service.reap_zombie_sessions()
    assert service.get_session(sid)["status"] == "error"
    assert service.resume_session(sid, workspace_id, user_id)["status"] == "idle"
