"""Schema v90 (#924): ``studio_chat_sessions.archived_at`` session archive.

Pins the migration side (fresh + upgrade from v89 land the nullable column
under the v90 name) and the query contract the service relies on: the
default list hides archived rows and the archive view lists only them, the
stamp flips exactly once per direction, a soft-deleted row can be neither
archived nor shown, and the resume claim refuses an archived row even though
its status is resumable.
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _archived_at_column(conn) -> dict | None:
    return conn.execute(
        "select is_nullable, data_type from information_schema.columns"
        " where table_schema=current_schema() and table_name='studio_chat_sessions'"
        " and column_name='archived_at'"
    ).fetchone()


def test_fresh_schema_has_nullable_archived_at_column() -> None:
    assert SCHEMA_VERSION >= 90
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _archived_at_column(conn)
    assert column is not None
    assert column["data_type"] == "timestamp with time zone"
    assert column["is_nullable"] == "YES"


@pytest.mark.fresh_schema
def test_upgrade_from_v89_adds_the_column() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 90")
        conn.execute("alter table studio_chat_sessions drop column if exists archived_at")
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _archived_at_column(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (90,)).fetchone()
    assert row is not None and row["name"] == "studio_chat_session_archive"
    assert column is not None and column["is_nullable"] == "YES"


@pytest.fixture
def seeded(job_db):
    workspace_id = job_db.create_workspace(name="AR WS")["id"]
    user_id = str(job_db.create_user("archive-user", password_hash=None)["id"])
    first = job_db.create_studio_chat_session(workspace_id, user_id, "fake-agent")
    second = job_db.create_studio_chat_session(workspace_id, user_id, "fake-agent")
    assert first is not None and second is not None
    return workspace_id, first, second


def _ids(rows: list[dict]) -> list[str]:
    return [row["id"] for row in rows]


def test_default_list_hides_archived_and_archive_view_shows_them(job_db, seeded) -> None:
    workspace_id, first, second = seeded
    assert job_db.list_studio_chat_sessions(workspace_id, archived=True) == []
    assert job_db.set_studio_chat_session_archived(first, True) is True
    assert _ids(job_db.list_studio_chat_sessions(workspace_id)) == [second]
    archived = job_db.list_studio_chat_sessions(workspace_id, archived=True)
    assert _ids(archived) == [first]
    assert archived[0]["archived_at"] is not None
    # A soft-deleted archived row leaves the archive view too.
    job_db.mark_studio_chat_session_deleted(first)
    assert job_db.list_studio_chat_sessions(workspace_id, archived=True) == []


def test_stamp_flips_once_per_direction(job_db, seeded) -> None:
    _workspace_id, first, _second = seeded
    assert job_db.set_studio_chat_session_archived(first, False) is False
    assert job_db.set_studio_chat_session_archived(first, True) is True
    assert job_db.set_studio_chat_session_archived(first, True) is False
    assert job_db.set_studio_chat_session_archived(first, False) is True
    row = job_db.get_studio_chat_session(first)
    assert row is not None and row["archived_at"] is None
    assert job_db.set_studio_chat_session_archived("missing", True) is False


def test_deleted_row_cannot_be_archived(job_db, seeded) -> None:
    _workspace_id, first, _second = seeded
    job_db.mark_studio_chat_session_deleted(first)
    assert job_db.set_studio_chat_session_archived(first, True) is False


def test_resume_claim_refuses_archived_row(job_db, seeded) -> None:
    _workspace_id, first, second = seeded
    for session_id in (first, second):
        job_db.update_studio_chat_session(session_id, status="closed")
    job_db.set_studio_chat_session_archived(first, True)
    assert job_db.claim_studio_chat_resume(first, max_active=32) is False
    assert job_db.claim_studio_chat_resume(second, max_active=32) is True
    row = job_db.get_studio_chat_session(first)
    assert row is not None and row["status"] == "closed"
    # Unarchive makes it claimable again (the resume path, not unarchive,
    # brings the runtime back).
    job_db.set_studio_chat_session_archived(first, False)
    assert job_db.claim_studio_chat_resume(first, max_active=32) is True
