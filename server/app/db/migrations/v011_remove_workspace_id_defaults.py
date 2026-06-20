import sqlite3

from server.app.db.migrations.models import Migration
from server.app.db.migrations.v004_workspace_dag_foreign_keys import (
    _EXECUTOR_LEASES,
    _TABLES,
    _column_names,
    _copy_table,
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _schema_matches(
    conn: sqlite3.Connection,
    source_table: str,
    replacement_table: str,
    create_sql: str,
) -> bool:
    """Return True only if the source table exists and matches the replacement schema."""
    if not _table_exists(conn, source_table):
        return False
    conn.execute(create_sql)
    try:
        source_cols = set(_column_names(conn, source_table))
        replacement_cols = set(_column_names(conn, replacement_table))
        return source_cols == replacement_cols
    finally:
        conn.execute(f"drop table {replacement_table}")


def _all_tables_rebuildable(conn: sqlite3.Connection) -> bool:
    """Preflight every table; only rebuild if the whole group matches."""
    for source_table, create_sql, replacement_table, _index_sqls, _drop_source in _TABLES:
        if not _schema_matches(conn, source_table, replacement_table, create_sql):
            return False

    source_table, create_sql, replacement_table, _index_sqls = _EXECUTOR_LEASES
    return _schema_matches(conn, source_table, replacement_table, create_sql)


def _apply(conn: sqlite3.Connection) -> None:
    # Delete the legacy default workspace first so its jobs/batches are removed
    # before the tables are rebuilt.
    if _table_exists(conn, "workspaces"):
        conn.execute("delete from workspaces where id = 'default'")

    # Rebuild tables that historically carried a 'default' default on
    # workspace_id. The schemas imported from v004 no longer include that
    # default after the v004 source file was corrected.
    #
    # Some legacy test databases have incomplete historical schemas. Rebuilding
    # only a subset would leave foreign keys pointing at dropped old tables, so
    # we preflight the whole group and skip the rebuild entirely unless every
    # table matches the final schema.
    if not _all_tables_rebuildable(conn):
        return

    for source_table, create_sql, replacement_table, index_sqls, drop_source in _TABLES:
        _copy_table(
            conn, source_table, replacement_table, create_sql, index_sqls, drop_source=drop_source
        )

    source_table, create_sql, replacement_table, index_sqls = _EXECUTOR_LEASES
    _copy_table(conn, source_table, replacement_table, create_sql, index_sqls)

    # Drop the kept old copies of jobs and node_runs now that executor_leases
    # has been rebuilt against the new tables.
    conn.execute("drop table if exists jobs__v004_old")
    conn.execute("drop table if exists node_runs__v004_old")


MIGRATION = Migration(
    version=11,
    name="remove_workspace_id_defaults",
    apply=_apply,
    rebuilds_fk=True,
)
