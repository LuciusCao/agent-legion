"""Schema v63: workspaces.preview_config_json (DDL-only via schema replay).

The workspace-level artifact preview config column backs the job-detail
left-panel preview checkboxes (settings section "preview", payload key
previewHidden). The SCHEMA_VERSION pin moved on to v64
(test_workspace_id_key_binding.py); this module pins the v63 record itself.
"""

from __future__ import annotations

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres


def test_v63_migration_record() -> None:
    # The v63 record survives later schema bumps; the moving SCHEMA_VERSION
    # pin lives in test_workspace_id_key_binding.py (v64).
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from schema_migrations where version=%s", (63,)).fetchone()
    assert row is not None
    assert row["name"] == "workspace_preview_config"


def test_v63_upgrade_adds_preview_config_column() -> None:
    """A pre-v63 database gains preview_config_json via init_db replay."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= %s", (63,))
        conn.execute("alter table workspaces drop column if exists preview_config_json")

    init_db(TEST_DATABASE_URL)

    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns where table_name='workspaces'"
            ).fetchall()
        }
        migration = conn.execute(
            "select name from schema_migrations where version=%s", (63,)
        ).fetchone()
    assert "preview_config_json" in columns
    assert migration is not None
    assert migration["name"] == "workspace_preview_config"
