"""Schema v71: versioned_entities entity_type CHECK widened for preview panels (#328)."""

from __future__ import annotations

import pytest
from psycopg import IntegrityError

from server.app.db.migration_registry import MIGRATIONS
from server.app.db.migrations.preview_panels import migrate_preview_panels
from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

_LEGACY_CHECK_DDL = """
alter table versioned_entities
  drop constraint if exists versioned_entities_entity_type_check;
alter table versioned_entities
  add constraint versioned_entities_entity_type_check
  check(entity_type in ('node_code', 'agent'))
"""

_INSERT_SQL = """
insert into versioned_entities(
  id, entity_type, workspace_id, entity_key, version, status,
  definition_json, definition_hash, created_by)
values (%s, %s, 'pp-mig-ws', 'default', 1, 'draft', '{}', 'h', 'u')
"""


def _insert_entity(conn, entity_type: str, row_id: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key)"
        " values ('pp-mig-ws', 'pp-mig-ws', 'wf') on conflict do nothing"
    )
    conn.execute(_INSERT_SQL, (row_id, entity_type))


def test_preview_panel_entity_type_accepted() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_entity(conn, "preview_panel", "pp-accept-1")
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select entity_type from versioned_entities where id='pp-accept-1'"
        ).fetchone()
    assert row is not None and row["entity_type"] == "preview_panel"


def test_unknown_entity_type_still_rejected() -> None:
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        _insert_entity(conn, "bogus_type", "pp-reject-1")


@pytest.mark.fresh_schema
def test_migration_widens_legacy_check_and_is_idempotent() -> None:
    # Simulate a v70 database: the CHECK without 'preview_panel'.
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(_LEGACY_CHECK_DDL)
    # The legacy CHECK rejects preview_panel rows (own transaction: an
    # IntegrityError aborts the surrounding transaction state).
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        _insert_entity(conn, "preview_panel", "pp-legacy")
    with write_transaction(TEST_DATABASE_URL) as conn:
        migrate_preview_panels(conn)
        _insert_entity(conn, "preview_panel", "pp-widened-1")
        # Replay: drop+add twice must not fail, the row survives.
        migrate_preview_panels(conn)


@pytest.mark.fresh_schema
def test_upgrade_from_v70_applies_the_widening() -> None:
    # Upgrade path: a database with no v71 record replays the schema file (a
    # no-op for the existing table) and re-runs the v71 migration. The
    # chain-tail pin moved to tests/db/test_studio_chat_schema.py (v72).
    assert MIGRATIONS[-1].name == "studio_chat_agent_config"
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from schema_migrations where version >= 71")
        conn.execute(_LEGACY_CHECK_DDL)
    init_db(TEST_DATABASE_URL)
    with write_transaction(TEST_DATABASE_URL) as conn:
        _insert_entity(conn, "preview_panel", "pp-upgraded-1")
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from schema_migrations where version = 71").fetchone()
    assert row is not None and row["name"] == "preview_panels"
