"""Studio chat session retention sweep (#1041).

Contract pinned here:
- default configuration (no document / 0) never removes anything;
- with a window configured, a closed session archived or soft-deleted longer
  than the window is physically removed together with its messages (the
  message table cascades on the session FK — no orphans);
- a session inside the window, an unarchived session, a never-stamped
  session, a stamped session still in a live status, and a stamped session
  that still owns an in-process runtime are all kept;
- a session deleted from the archive view is timed from its deletion, not
  from the older archive stamp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from server.app.services.instance_settings_store import InstanceSettingsStore
from server.app.studio_chat.retention import (
    studio_chat_retention_days,
    sweep_expired_chat_sessions,
)
from server.app.studio_chat.service import StudioChatService

WINDOW_DAYS = 30


@pytest.fixture
def chat(job_db, settings):
    service = StudioChatService(job_db, settings, None)
    workspace_id = job_db.create_workspace(name="Retention WS")["id"]
    user_id = str(job_db.create_user("retention-user", password_hash=None)["id"])
    yield service, workspace_id, user_id
    service.shutdown()


def _set_window(job_db, days: int) -> None:
    InstanceSettingsStore(job_db).put({"studio_chat_retention_days": days})


def _closed_session(job_db, workspace_id: str, user_id: str, *, messages: int = 2) -> str:
    session_id = job_db.create_studio_chat_session(workspace_id, user_id, "fake-agent")
    for index in range(messages):
        job_db.append_studio_chat_message(session_id, "text", "user", {"text": f"m{index}"})
    job_db.update_studio_chat_session(session_id, status="closed")
    return session_id


def _later(days: int) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def _message_rows(job_db, session_id: str) -> int:
    with job_db.connect() as conn:
        row = conn.execute(
            "select count(*) as n from studio_chat_messages where session_id=%s", (session_id,)
        ).fetchone()
    return int(row["n"])


def test_default_configuration_never_purges(chat, job_db) -> None:
    service, workspace_id, user_id = chat
    session_id = _closed_session(job_db, workspace_id, user_id)
    assert job_db.set_studio_chat_session_archived(session_id, True)

    assert studio_chat_retention_days(job_db) == 0
    assert sweep_expired_chat_sessions(service, now=_later(36500)) == 0
    _set_window(job_db, 0)
    assert sweep_expired_chat_sessions(service, now=_later(36500)) == 0
    assert job_db.get_studio_chat_session(session_id) is not None
    assert _message_rows(job_db, session_id) == 2


def test_expired_archived_and_deleted_sessions_are_purged_with_messages(chat, job_db) -> None:
    service, workspace_id, user_id = chat
    archived = _closed_session(job_db, workspace_id, user_id)
    deleted = _closed_session(job_db, workspace_id, user_id, messages=3)
    assert job_db.set_studio_chat_session_archived(archived, True)
    assert job_db.mark_studio_chat_session_deleted(deleted)
    _set_window(job_db, WINDOW_DAYS)

    # Inside the window: kept.
    assert sweep_expired_chat_sessions(service, now=_later(WINDOW_DAYS - 1)) == 0
    assert job_db.get_studio_chat_session(archived) is not None

    assert sweep_expired_chat_sessions(service, now=_later(WINDOW_DAYS + 1)) == 2
    for session_id in (archived, deleted):
        assert job_db.get_studio_chat_session(session_id) is None
        assert _message_rows(job_db, session_id) == 0
    # Idempotent: nothing left to purge.
    assert sweep_expired_chat_sessions(service, now=_later(WINDOW_DAYS + 1)) == 0


def test_unarchived_and_unstamped_sessions_are_kept(chat, job_db) -> None:
    service, workspace_id, user_id = chat
    restored = _closed_session(job_db, workspace_id, user_id)
    assert job_db.set_studio_chat_session_archived(restored, True)
    assert job_db.set_studio_chat_session_archived(restored, False)
    listed = _closed_session(job_db, workspace_id, user_id)
    _set_window(job_db, WINDOW_DAYS)

    assert sweep_expired_chat_sessions(service, now=_later(WINDOW_DAYS * 10)) == 0
    assert job_db.get_studio_chat_session(restored) is not None
    assert job_db.get_studio_chat_session(listed) is not None


def test_live_status_and_live_runtime_are_never_purged(chat, job_db) -> None:
    service, workspace_id, user_id = chat
    running = _closed_session(job_db, workspace_id, user_id)
    assert job_db.set_studio_chat_session_archived(running, True)
    # Out-of-band state: stamped but the status machine says live.
    job_db.update_studio_chat_session(running, status="running")
    with_runtime = _closed_session(job_db, workspace_id, user_id)
    assert job_db.set_studio_chat_session_archived(with_runtime, True)
    purgeable = _closed_session(job_db, workspace_id, user_id)
    assert job_db.set_studio_chat_session_archived(purgeable, True)
    _set_window(job_db, WINDOW_DAYS)

    sentinel = object()
    service._runtimes[with_runtime] = sentinel  # type: ignore[assignment]
    try:
        assert sweep_expired_chat_sessions(service, now=_later(WINDOW_DAYS + 1)) == 1
    finally:
        service._runtimes.pop(with_runtime, None)
    assert job_db.get_studio_chat_session(running) is not None
    assert job_db.get_studio_chat_session(with_runtime) is not None
    assert job_db.get_studio_chat_session(purgeable) is None


def test_deleted_from_archive_is_timed_from_deletion(chat, job_db) -> None:
    service, workspace_id, user_id = chat
    session_id = _closed_session(job_db, workspace_id, user_id)
    assert job_db.set_studio_chat_session_archived(session_id, True)
    with job_db.connect() as conn:
        conn.execute(
            "update studio_chat_sessions set archived_at = current_timestamp - interval '40 days'"
            " where id=%s",
            (session_id,),
        )
    assert job_db.mark_studio_chat_session_deleted(session_id)
    _set_window(job_db, WINDOW_DAYS)

    # The archive stamp alone is past the window, the deletion is not.
    assert sweep_expired_chat_sessions(service) == 0
    assert job_db.get_studio_chat_session(session_id) is not None
    assert sweep_expired_chat_sessions(service, now=_later(WINDOW_DAYS + 1)) == 1


def test_retention_days_degrades_malformed_values_to_disabled(job_db) -> None:
    for value in (-1, "30", True, 1.5):
        _set_window(job_db, value)  # type: ignore[arg-type]
        assert studio_chat_retention_days(job_db) == 0
    _set_window(job_db, 7)
    assert studio_chat_retention_days(job_db) == 7
