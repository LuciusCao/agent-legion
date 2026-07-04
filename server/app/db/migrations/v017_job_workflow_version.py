from __future__ import annotations

import sqlite3

from server.app.db.migrations.models import Migration

VERSION = 17
NAME = "job_workflow_version"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "select 1 from sqlite_master where type='table' and name=?", (name,)
        ).fetchone()
        is not None
    )


def apply(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "jobs"):
        return
    columns = {row["name"] for row in conn.execute("pragma table_info(jobs)").fetchall()}
    if "workflow_version" not in columns:
        conn.execute("alter table jobs add column workflow_version integer")
    if _table_exists(conn, "workflow_revisions"):
        conn.execute(
            """
            update jobs
            set workflow_version = (
              select workflow_revisions.version
              from workflow_revisions
              where workflow_revisions.id = jobs.workflow_revision_id
            )
            where workflow_version is null
              and coalesce(workflow_revision_id, '') != ''
              and exists (
                select 1
                from workflow_revisions
                where workflow_revisions.id = jobs.workflow_revision_id
              )
            """
        )


MIGRATION = Migration(VERSION, NAME, apply)
