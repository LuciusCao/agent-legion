"""Schema v75 (#416): the studio_publish_requests lifecycle at the DB layer.

Route-level behavior (auth matrix, publish gates) lives in
tests/routes/test_studio_publish_requests.py; this file pins the migration
record, the table shape, and the queries-layer state machine (supersede /
lazy expiry / atomic resolve) against real Postgres.
"""

from __future__ import annotations

import pytest
from psycopg import IntegrityError

from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def test_schema_v75_recorded() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from schema_migrations where version=%s", (75,)).fetchone()
    assert row is not None
    assert row["name"] == "studio_publish_requests"


def test_publish_requests_columns() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='studio_publish_requests'"
            ).fetchall()
        }
    assert columns == {
        "id",
        "workspace_id",
        "chat_session_id",
        "status",
        "created_by",
        "result_revision_id",
        "created_at",
        "expires_at",
        "resolved_at",
    }


def test_status_check_rejects_unknown_states() -> None:
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into studio_publish_requests(workspace_id, status, expires_at)"
            " values ('demo_workflow', 'bogus', now() + interval '1 minute')"
        )
