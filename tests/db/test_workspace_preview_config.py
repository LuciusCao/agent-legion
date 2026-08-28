"""Schema v63: workspaces.preview_config_json (DDL-only via schema replay).

The workspace-level artifact preview config column backs the job-detail
left-panel preview checkboxes (settings section "preview", payload key
previewHidden). This module owns the SCHEMA_VERSION pin per the moving-pin
convention (see test_retire_global_register_tokens_migration.py history).
"""

from __future__ import annotations

import pytest

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres


def test_schema_version_pin() -> None:
    # The latest-migration record pin moved through
    # test_retire_global_register_tokens_migration.py (v58) →
    # test_jobs_run_id_index.py (v59) → the DDL-only v60/v61 entries →
    # test_workspace_id_key_binding.py (v62) → here (v63, DDL-only).
    assert SCHEMA_VERSION == 63
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "workspace_preview_config"


def test_v63_upgrade_adds_preview_config_column() -> None:
    """One-version-behind database gains preview_config_json via init_db replay."""
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))
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
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert "preview_config_json" in columns
    assert migration is not None
    assert migration["name"] == "workspace_preview_config"
