"""Schema parity: an upgraded database must match a fresh database exactly.

The upgrade contract of ``init_db`` (server/app/db/schema.py): an older
database is upgraded by replaying the FULL ``postgres_schema.sql`` first, then
running only the registry migrations with ``version > max(applied)``. That
split implies a rule with no mechanical enforcement — every DDL entry in the
schema file must be valid against EVERY historical database shape, because
the file replays before any migration runs. The v59 review caught two
violations of that rule in one index placement (first in the schema file,
where a v52 database still names the column ``batch_id``; then inside the
v53 migration, which v53-v58 databases skip entirely — see
docs/reviews/2026-08-26-perf-quality-review.md).

This test guards the whole failure CLASS instead of one instance: it builds a
database one version behind (SCHEMA_VERSION - 1), upgrades it via init_db,
and diffs the resulting catalog (columns, indexes) against a fresh init_db
database. Any future migration that lands DDL on the wrong side of the
schema-file/migration split shows up here as a catalog mismatch, whatever
version or table it touches.

The "one version behind" shape is built by replaying the current schema and
undoing the newest migration's effects — the same construction
tests/db/test_jobs_run_id_index.py uses, generalized. When SCHEMA_VERSION
moves on, extend ``_undo_newest_migration`` for the new newest migration;
the ``_NEWEST_MIGRATION_UNDO`` assertion below fails loudly when it is
forgotten.

The scratch schema (``<worker>_parity``) is dropped and rebuilt inside the
test and torn down afterwards; it shares the per-worktree test database but
never touches the worker's main schema.
"""

from __future__ import annotations

import os
from urllib.parse import quote

import psycopg
import pytest
from psycopg import sql

from server.app.db.schema import SCHEMA_VERSION, init_db
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import BASE_DATABASE_URL, TEST_DATABASE_URL, TEST_SCHEMA

# Effects the newest migration (v70, retire_workflow_key_columns) must
# leave behind so the undo step rewinds a current-shape database to exactly
# SCHEMA_VERSION-1. v70 is DDL-only: it drops the workflow_key column from
# nine tables, rebuilds four composite PKs without it, and swaps the retired
# key-leading indexes for workspace-keyed twins. The undo step re-adds the
# columns (with their v69-era types), restores the old composite PKs, drops
# the twin indexes the fresh schema replays, and recreates the retired
# indexes so both catalogs match after the upgrade under test.
_NEWEST_MIGRATION_TABLES: tuple[str, ...] = ()
_NEWEST_MIGRATION_COLUMNS: tuple[tuple[str, str, str], ...] = ()
_NEWEST_MIGRATION_INDEXES: tuple[str, ...] = (
    "idx_jobs_workspace_status",
    "idx_jobs_workspace_source",
    "idx_jobs_workspace_active_marks",
    # Same-name old-shape indexes the rewind must restore (v69 shape): the
    # upgrade replay skips existing names and the column drop auto-drops
    # them, so the v70 migration has to recreate the terminal shapes — the
    # rewind simulating v69 must carry the OLD shapes or the upgrade path
    # under test never exercises the recreation (Codex P2 #334).
    "idx_workflow_revisions_active",
    "idx_agent_requests_node_active",
    "idx_executor_leases_workflow_node_active",
)
# Old-shape DDL the rewind recreates so the (SCHEMA_VERSION-1) database is a
# faithful v69: the same-name indexes carry their workflow_key shapes, the
# revision unique constraint survives with its column, and the four
# composite-PK tables keep their three-column keys — the migration's
# drop-PK → drop-column → add-narrowed-PK loop only runs against the old
# keys (subagent review #334: a narrowed-PK "v69" never exercises it).
_NEWEST_MIGRATION_UNDO_DDL: tuple[str, ...] = (
    "create index if not exists idx_workflow_revisions_active"
    " on workflow_revisions(workspace_id, workflow_key, status)",
    "create index if not exists idx_agent_requests_node_active"
    " on agent_execution_requests(workspace_id, workflow_key, node_key, state)",
    "create index if not exists idx_executor_leases_workflow_node_active"
    " on executor_leases(workspace_id, workflow_key, node_key, status, expires_at)",
    "alter table workflow_revisions add constraint"
    " workflow_revisions_workspace_id_workflow_key_version_key"
    " unique (workspace_id, workflow_key, version)",
    "alter table workspace_node_limits drop constraint workspace_node_limits_pkey",
    "alter table workspace_node_limits add primary key (workspace_id, workflow_key, node_key)",
    "alter table workspace_node_routes drop constraint workspace_node_routes_pkey",
    "alter table workspace_node_routes add primary key (workspace_id, workflow_key, node_key)",
    "alter table workspace_node_capacities drop constraint workspace_node_capacities_pkey",
    "alter table workspace_node_capacities add primary key (workspace_id, workflow_key, node_key)",
    "alter table workspace_job_node_status_counts drop constraint"
    " workspace_job_node_status_counts_pkey",
    "alter table workspace_job_node_status_counts add primary key"
    " (workspace_id, workflow_key, node_key, status)",
)
_NEWEST_MIGRATION_NAME = "retire_workflow_key_columns"
# (table, column DDL) pairs re-created by the undo step.
_NEWEST_MIGRATION_COLUMNS_RESTORE: tuple[tuple[str, str], ...] = (
    ("runs", "workflow_key text not null default ''"),
    ("jobs", "workflow_key text not null default ''"),
    ("executor_leases", "workflow_key text not null default ''"),
    ("agent_execution_requests", "workflow_key text not null default ''"),
    ("workflow_revisions", "workflow_key text not null default ''"),
    ("workspace_node_limits", "workflow_key text not null default ''"),
    ("workspace_node_routes", "workflow_key text not null default ''"),
    ("workspace_node_capacities", "workflow_key text not null default ''"),
    ("workspace_job_node_status_counts", "workflow_key text not null default ''"),
)

# (table, column, data_type) and (table, index, indexdef) triples.
_CatalogColumns = set[tuple[str, str, str]]
_CatalogIndexes = set[tuple[str, str, str]]
# (table, constraint_name, definition) — the unique/PK constraint layer
# (column drops auto-drop dependent constraints; index-only diffs miss them).
_CatalogConstraints = set[tuple[str, str, str]]


def _undo_newest_migration(database_dsn: str) -> None:
    """Rewind a current-shape database to SCHEMA_VERSION - 1."""
    with write_transaction(database_dsn) as conn:
        for table in _NEWEST_MIGRATION_TABLES:
            conn.execute(f"drop table if exists {table}")
        for table, column, _data_type in _NEWEST_MIGRATION_COLUMNS:
            conn.execute(f"alter table {table} drop column if exists {column}")
        for index_name in _NEWEST_MIGRATION_INDEXES:
            conn.execute(f"drop index if exists {index_name}")
        for table, column_ddl in _NEWEST_MIGRATION_COLUMNS_RESTORE:
            conn.execute(f"alter table {table} add column if not exists {column_ddl}")
        for statement in _NEWEST_MIGRATION_UNDO_DDL:
            conn.execute(statement)
        conn.execute("delete from schema_migrations where version=%s", (SCHEMA_VERSION,))


def _catalog_columns(schema_name: str) -> _CatalogColumns:
    with read_connection(TEST_DATABASE_URL) as conn:
        return {
            (str(row["table_name"]), str(row["column_name"]), str(row["data_type"]))
            for row in conn.execute(
                "select table_name, column_name, data_type from information_schema.columns"
                " where table_schema=%s",
                (schema_name,),
            ).fetchall()
        }


def _catalog_indexes(schema_name: str) -> _CatalogIndexes:
    with read_connection(TEST_DATABASE_URL) as conn:
        return {
            # indexdef embeds the schema name (e.g. "ON agent_legion_test_gw0.
            # jobs"); strip it so the two schemas compare by structure.
            (
                str(row["tablename"]),
                str(row["indexname"]),
                str(row["indexdef"]).replace(f"ON {schema_name}.", "ON "),
            )
            for row in conn.execute(
                "select tablename, indexname, indexdef from pg_indexes where schemaname=%s",
                (schema_name,),
            ).fetchall()
        }


def _catalog_constraints(schema_name: str) -> _CatalogConstraints:
    with read_connection(TEST_DATABASE_URL) as conn:
        return {
            (
                str(row["table_name"]),
                str(row["constraint_name"]),
                # conkey ordinal form: name-independent across shapes whose
                # column order differs (the unique constraint's definition
                # embeds no schema name but does embed column names).
                str(row["constraintdef"]),
            )
            for row in conn.execute(
                """
                select tc.table_name, tc.constraint_name, pg_get_constraintdef(c.oid) as constraintdef
                from information_schema.table_constraints tc
                join pg_constraint c on c.conname = tc.constraint_name
                where tc.table_schema=%s and tc.constraint_type in ('UNIQUE', 'PRIMARY KEY')
                """,
                (schema_name,),
            ).fetchall()
        }


def test_newest_migration_undo_inventory_is_current() -> None:
    """The undo step must cover the actual newest registry entry; a version
    bump without extending _undo_newest_migration fails here first."""
    from server.app.db.migration_registry import MIGRATIONS

    newest = MIGRATIONS[-1]
    assert newest.version == SCHEMA_VERSION, "registry tail must match SCHEMA_VERSION"
    assert newest.name == _NEWEST_MIGRATION_NAME, (
        f"SCHEMA_VERSION moved to {newest.version} ({newest.name}): extend"
        " _undo_newest_migration in this file to rewind the new newest"
        " migration's effects (indexes/columns/tables it creates), then bump"
        " _NEWEST_MIGRATION_INDEXES/_NEWEST_MIGRATION_NAME here."
    )


@pytest.mark.postgres
@pytest.mark.fresh_schema
def test_upgraded_database_matches_fresh_catalog() -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    parity_schema = f"agent_legion_test_{worker}_parity"
    separator = "&" if "?" in BASE_DATABASE_URL else "?"
    parity_dsn = f"{BASE_DATABASE_URL}{separator}options={quote(f'-csearch_path={parity_schema}')}"

    # Build the (SCHEMA_VERSION - 1) shape in the scratch schema, then run
    # the upgrade path under test. The advisory lock in init_db is keyed on
    # current_database() but taken inside the scratch schema's own
    # schema_migrations transaction, so it never queues against the main
    # schema's migrations.
    with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as admin:
        admin.execute(
            sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(parity_schema))
        )
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(parity_schema)))
    init_db(parity_dsn)  # current shape (SCHEMA_VERSION)
    _undo_newest_migration(parity_dsn)  # rewind to SCHEMA_VERSION - 1
    init_db(parity_dsn)  # the upgrade path under test
    try:
        fresh_columns = _catalog_columns(TEST_SCHEMA)
        fresh_indexes = _catalog_indexes(TEST_SCHEMA)
        fresh_constraints = _catalog_constraints(TEST_SCHEMA)
        upgraded_columns = _catalog_columns(parity_schema)
        upgraded_indexes = _catalog_indexes(parity_schema)
        upgraded_constraints = _catalog_constraints(parity_schema)
    finally:
        # Leave no scratch schema behind for later tests on this worker.
        with psycopg.connect(BASE_DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(parity_schema))
            )

    assert upgraded_columns == fresh_columns, (
        "columns diverge between fresh and upgraded databases:\n"
        f"only fresh: {sorted(fresh_columns - upgraded_columns)}\n"
        f"only upgraded: {sorted(upgraded_columns - fresh_columns)}"
    )
    assert upgraded_indexes == fresh_indexes, (
        "indexes diverge between fresh and upgraded databases:\n"
        f"only fresh: {sorted(fresh_indexes - upgraded_indexes)}\n"
        f"only upgraded: {sorted(upgraded_indexes - fresh_indexes)}"
    )
    assert upgraded_constraints == fresh_constraints, (
        "constraints diverge between fresh and upgraded databases:\n"
        f"only fresh: {sorted(fresh_constraints - upgraded_constraints)}\n"
        f"only upgraded: {sorted(upgraded_constraints - fresh_constraints)}"
    )
