#!/usr/bin/env python3
"""One-time, offline import from the final SQLite schema to PostgreSQL."""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from scripts.sqlite_import_support import ensure_empty, remove_schema_bootstrap
from server.app.db.connection import close_database_pools
from server.app.db.schema import init_db

TABLES: tuple[str, ...] = (
    "videos",
    "phase_runs",
    "transcription_runs",
    "packages",
    "batches",
    "workspaces",
    "workspace_executor_allocations",
    "workspace_node_bindings",
    "workspace_node_limits",
    "job_batches",
    "jobs",
    "job_nodes",
    "node_runs",
    "executor_leases",
    "workspace_packages",
    "workflow_revisions",
    "node_run_token_usage",
    "remote_workers",
    "remote_executions",
    "worker_control_state",
    "artifacts",
    "artifact_refs",
    "node_shards",
)
IDENTITY_TABLES: tuple[str, ...] = (
    "phase_runs",
    "transcription_runs",
    "packages",
    "job_nodes",
    "node_runs",
    "workspace_packages",
    "node_run_token_usage",
)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("postgres_url")
    parser.add_argument(
        "--truncate-target",
        action="store_true",
        help="delete existing target rows before import (destructive)",
    )
    return parser.parse_args(argv)


def _sqlite_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute("select name from sqlite_master where type='table'")
    }


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f'pragma table_info("{table}")')]


def _postgres_columns(conn: psycopg.Connection[Any], table: str) -> list[str]:
    rows = conn.execute(
        "select column_name from information_schema.columns"
        " where table_schema=current_schema() and table_name=%s order by ordinal_position",
        (table,),
    ).fetchall()
    return [str(row["column_name"]) for row in rows]


def _truncate(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        sql.SQL("truncate {} restart identity cascade").format(
            sql.SQL(", ").join(map(sql.Identifier, reversed(TABLES)))
        )
    )


def _copy_table(
    source: sqlite3.Connection,
    target: psycopg.Connection[Any],
    table: str,
    source_tables: set[str],
) -> int:
    if table not in source_tables:
        return 0
    source_columns = _sqlite_columns(source, table)
    target_columns = set(_postgres_columns(target, table))
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        return 0
    identifiers = sql.SQL(", ").join(map(sql.Identifier, columns))
    selected_columns = ", ".join('"' + column + '"' for column in columns)
    rows = source.execute(f'select {selected_columns} from "{table}"').fetchall()
    if not rows:
        return 0
    statement = sql.SQL("insert into {} ({}) values ({})").format(
        sql.Identifier(table),
        identifiers,
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    with target.cursor() as cursor:
        cursor.executemany(statement, [tuple(row[column] for column in columns) for row in rows])
    return len(rows)


def _reset_identities(conn: psycopg.Connection[Any]) -> None:
    for table in IDENTITY_TABLES:
        conn.execute(
            sql.SQL(
                "select setval(pg_get_serial_sequence({}, 'id'),"
                " greatest(coalesce(max(id), 1), 1), count(*) > 0) from {}"
            ).format(sql.Literal(table), sql.Identifier(table))
        )


def import_database(sqlite_path: Path, postgres_url: str, *, truncate: bool) -> dict[str, int]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    init_db(postgres_url)
    close_database_pools()
    source_uri = f"file:{sqlite_path.resolve()}?mode=ro"
    with (
        closing(sqlite3.connect(source_uri, uri=True)) as source,
        psycopg.connect(postgres_url, row_factory=dict_row) as target,
    ):
        source.row_factory = sqlite3.Row
        source.execute("begin")
        if truncate:
            _truncate(target)
        else:
            remove_schema_bootstrap(target, TABLES)
            ensure_empty(target, TABLES)
        source_tables = _sqlite_tables(source)
        counts = {table: _copy_table(source, target, table, source_tables) for table in TABLES}
        if "job_event_seq" in source_tables:
            row = source.execute("select value from job_event_seq where id=1").fetchone()
            if row is not None:
                target.execute("update job_event_seq set value=%s where id=1", (row["value"],))
        _reset_identities(target)
        return counts


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    counts = import_database(
        args.sqlite_path,
        args.postgres_url,
        truncate=args.truncate_target,
    )
    for table, count in counts.items():
        if count:
            print(f"{table}: {count}")
    print(f"Imported {sum(counts.values())} rows into PostgreSQL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
