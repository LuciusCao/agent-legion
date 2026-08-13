"""Schema v41: auth_scoped_tokens scoped bearer token table."""

from __future__ import annotations

from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def test_auth_scoped_tokens_table_exists() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='auth_scoped_tokens'"
            ).fetchall()
        }
    assert columns == {
        "token_hash",
        "user_id",
        "scope",
        "expires_at",
        "revoked_at",
        "created_at",
    }
