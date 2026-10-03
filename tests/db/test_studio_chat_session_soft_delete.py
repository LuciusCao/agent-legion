"""Schema v89 (#872): ``studio_chat_sessions.deleted_at`` soft delete.

Pins the migration side (fresh + upgrade from v88 land the nullable column,
chain tail carries the v89 name) and the query contract the service relies
on: the list filters stamped rows, the stamp has exactly one winner, and the
resume claim refuses a stamped row even though its status is resumable.
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def _deleted_at_column(conn) -> dict | None:
    return conn.execute(
        "select is_nullable, data_type from information_schema.columns"
        " where table_schema=current_schema() and table_name='studio_chat_sessions'"
        " and column_name='deleted_at'"
    ).fetchone()


def test_fresh_schema_has_nullable_deleted_at_column() -> None:
    assert SCHEMA_VERSION >= 89
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _deleted_at_column(conn)
    assert column is not None
    assert column["data_type"] == "timestamp with time zone"
    assert column["is_nullable"] == "YES"


@pytest.mark.fresh_schema
def test_upgrade_from_v88_adds_the_column() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 89")
        conn.execute("alter table studio_chat_sessions drop column if exists deleted_at")
    init_db(TEST_DATABASE_URL)
    with read_connection(TEST_DATABASE_URL) as conn:
        column = _deleted_at_column(conn)
        row = conn.execute("select name from schema_migrations where version=%s", (89,)).fetchone()
    assert row is not None and row["name"] == "studio_chat_session_soft_delete"
    assert column is not None and column["is_nullable"] == "YES"


@pytest.fixture
def seeded(job_db):
    workspace_id = job_db.create_workspace(default_workflow_key="demo_workflow", name="SD WS")["id"]
    user_id = str(job_db.create_user("soft-delete-user", password_hash=None)["id"])
    first = job_db.create_studio_chat_session(workspace_id, user_id, "fake-agent")
    second = job_db.create_studio_chat_session(workspace_id, user_id, "fake-agent")
    assert first is not None and second is not None
    return workspace_id, first, second


def test_list_filters_stamped_rows(job_db, seeded) -> None:
    workspace_id, first, second = seeded
    assert {row["id"] for row in job_db.list_studio_chat_sessions(workspace_id)} == {
        first,
        second,
    }
    assert job_db.mark_studio_chat_session_deleted(first) is True
    assert [row["id"] for row in job_db.list_studio_chat_sessions(workspace_id)] == [second]
    # The raw row read stays available to the service's internal paths.
    row = job_db.get_studio_chat_session(first)
    assert row is not None and row["deleted_at"] is not None


def test_stamp_has_exactly_one_winner(job_db, seeded) -> None:
    _workspace_id, first, _second = seeded
    assert job_db.mark_studio_chat_session_deleted(first) is True
    assert job_db.mark_studio_chat_session_deleted(first) is False
    assert job_db.mark_studio_chat_session_deleted("missing") is False


def test_resume_claim_refuses_stamped_row(job_db, seeded) -> None:
    _workspace_id, first, second = seeded
    for session_id in (first, second):
        job_db.update_studio_chat_session(session_id, status="closed")
    job_db.mark_studio_chat_session_deleted(first)
    assert job_db.claim_studio_chat_resume(first, max_active=32) is False
    assert job_db.claim_studio_chat_resume(second, max_active=32) is True
    row = job_db.get_studio_chat_session(first)
    assert row is not None and row["status"] == "closed"
