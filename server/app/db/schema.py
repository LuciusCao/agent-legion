from __future__ import annotations

from pathlib import Path

from server.app.db.connection import DatabaseDsn
from server.app.db.migration_registry import MIGRATIONS
from server.app.db.migrations.workspace_settings_retirement import (
    drop_retired_workspace_setting_columns,
)
from server.app.db.schema_guard import guard_shared_db
from server.app.db.transaction import write_transaction

SCHEMA_VERSION = 65
_SCHEMA_FILE = Path(__file__).with_name("postgres_schema.sql")


def init_db(database_dsn: DatabaseDsn) -> None:
    """Initialize or upgrade the PostgreSQL schema under a migration lock.

    Fresh databases apply the whole idempotent ``postgres_schema.sql`` and
    every migration in order, recording one ``schema_migrations`` row per
    version. Databases at an older version still replay the full DDL file
    (that remains the DDL upgrade mechanism) but only run data migrations
    with ``version > max(applied)`` — no more full replay of data
    migrations on upgrade. Databases recorded at the current version
    (including legacy single-row installs) are a no-op. The bare shared
    database additionally requires the schema_guard opt-in.
    """
    guard_shared_db(database_dsn)
    with write_transaction(database_dsn) as conn:
        # Serialize migrations per database, not cluster-wide: worktrees run
        # against dedicated databases (tests/postgres_support.py derives one
        # per worktree) and must not queue on each other's schema lock.
        conn.execute(
            "select pg_advisory_xact_lock(hashtext('agent-legion-schema-' || current_database()))"
        )
        conn.execute(
            """
            create table if not exists schema_migrations (
              version integer primary key,
              name text not null,
              applied_at timestamptz not null default current_timestamp
            )
            """
        )
        applied_versions = {
            row["version"]
            for row in conn.execute("select version from schema_migrations").fetchall()
        }
        if applied_versions and max(applied_versions) >= SCHEMA_VERSION:
            return
        conn.execute(_SCHEMA_FILE.read_text(encoding="utf-8"))
        # Legacy single-row installs recorded only their final version, so a
        # membership check would replay every retired data migration on
        # upgrade; anything at or below the high-water mark is already done.
        max_applied = max(applied_versions) if applied_versions else 0
        for migration in MIGRATIONS:
            if migration.version <= max_applied:
                continue
            if migration.apply is not None:
                migration.apply(conn)
            conn.execute(
                "insert into schema_migrations(version, name) values (%s, %s)",
                (migration.version, migration.name),
            )
        # Legacy final cleanup (historically trailed the whole replay): the
        # cms_config_json column is superseded by workspace_cms_config's
        # resource rows and must not survive any install path.
        conn.execute("alter table workspaces drop column if exists cms_config_json")
        # v64 retirement sweep (module docstring explains the post-chain timing).
        drop_retired_workspace_setting_columns(conn)
        # Retired-table parity sweep: postgres_schema.sql still CREATES these
        # tables so older data migrations can replay against them (v34 reads
        # job_batches, v47 harvests the executor tables), and their owning
        # migrations (v53 runs cutover, v47 executor retirement) drop them.
        # A database recorded at v48-v58 skips those migrations but still
        # replays the schema file, resurrecting empty copies that a fresh
        # database never has — upgraded databases must not stay dirtier than
        # fresh ones (guarded by tests/db/test_schema_upgrade_parity.py).
        for retired in ("job_batches", "workspace_executor_allocations", "workspace_node_bindings"):
            conn.execute(f"drop table if exists {retired}")
