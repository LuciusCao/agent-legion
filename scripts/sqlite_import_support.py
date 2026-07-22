from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import sql


def count_orphan_artifact_refs(source: sqlite3.Connection, source_tables: set[str]) -> int:
    """Count artifact_refs rows whose hash has no matching artifacts row.

    PostgreSQL enforces ``artifact_refs.hash -> artifacts(hash)`` while SQLite
    did not, so legacy databases can contain orphans that would abort the copy.
    """
    if "artifact_refs" not in source_tables:
        return 0
    if "artifacts" not in source_tables:
        row = source.execute("select count(*) from artifact_refs").fetchone()
        return int(row[0])
    row = source.execute(
        "select count(*) from artifact_refs where hash not in (select hash from artifacts)"
    ).fetchone()
    return int(row[0])


def populated_tables(conn: psycopg.Connection[Any], tables: Sequence[str]) -> list[str]:
    return [
        table
        for table in tables
        if (
            conn.execute(
                sql.SQL("select exists(select 1 from {} limit 1) as populated").format(
                    sql.Identifier(table)
                )
            ).fetchone()
            or {"populated": False}
        )["populated"]
    ]


def ensure_empty(conn: psycopg.Connection[Any], tables: Sequence[str]) -> None:
    populated = populated_tables(conn, tables)
    if populated:
        raise RuntimeError(
            "target PostgreSQL database is not empty: "
            + ", ".join(populated)
            + "; use a fresh database or pass --truncate-target"
        )


def remove_schema_bootstrap(conn: psycopg.Connection[Any], tables: Sequence[str]) -> None:
    """Remove the exact built-in seed rows from an otherwise fresh target."""
    if set(populated_tables(conn, tables)) - {"workspaces", "workflow_revisions"}:
        return
    workspaces = conn.execute("select id from workspaces").fetchall()
    revisions = conn.execute("select id from workflow_revisions").fetchall()
    if [row["id"] for row in workspaces] == ["question_comprehension"] and [
        row["id"] for row in revisions
    ] == ["question_comprehension:question_comprehension_info:v1"]:
        conn.execute("delete from workflow_revisions")
        conn.execute("delete from workspaces")
