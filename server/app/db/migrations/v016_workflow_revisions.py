from __future__ import annotations

import sqlite3

from server.app.db.migrations.models import Migration

VERSION = 16
NAME = "workflow_revisions"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "select 1 from sqlite_master where type='table' and name=?", (name,)
        ).fetchone()
        is not None
    )


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists workflow_revisions (
          id text primary key,
          workspace_id text not null,
          workflow_key text not null,
          version integer not null,
          status text not null check(status in ('draft', 'active', 'archived')),
          definition_json text not null,
          definition_hash text not null,
          created_at text not null default current_timestamp,
          published_at text,
          unique(workspace_id, workflow_key, version),
          foreign key(workspace_id) references workspaces(id) on delete cascade
        )
        """
    )
    conn.execute(
        "create index if not exists idx_workflow_revisions_active on workflow_revisions(workspace_id, workflow_key, status)"
    )
    if _table_exists(conn, "jobs"):
        for column, ddl in (
            (
                "workflow_revision_id",
                "alter table jobs add column workflow_revision_id text not null default ''",
            ),
            (
                "workflow_definition_hash",
                "alter table jobs add column workflow_definition_hash text not null default ''",
            ),
            (
                "workflow_definition_snapshot_json",
                "alter table jobs add column workflow_definition_snapshot_json text not null default ''",
            ),
            ("outcome", "alter table jobs add column outcome text not null default ''"),
        ):
            existing = {row["name"] for row in conn.execute("pragma table_info(jobs)").fetchall()}
            if column not in existing:
                conn.execute(ddl)
    if _table_exists(conn, "workspaces"):
        conn.execute(
            "insert or ignore into workspaces(id, name, default_workflow_key, default_entity) "
            "values ('question_comprehension', '题目审题信息', 'question_comprehension_info', 'question')"
        )
        _backfill_workspace_revisions(conn)


def _backfill_workspace_revisions(conn: sqlite3.Connection) -> None:
    from pathlib import Path

    from server.app.services.workflow_revisions import definition_hash, serialize_definition
    from server.app.workflows.definition import load_workflow_definition

    root_dir = Path(__file__).resolve().parents[4]
    workflow_dir = root_dir / "config" / "workflows"
    workspaces = conn.execute("select id, default_workflow_key from workspaces").fetchall()
    for workspace in workspaces:
        workspace_id = str(workspace["id"])
        workflow_key = str(workspace["default_workflow_key"])
        existing = conn.execute(
            """
            select 1 from workflow_revisions
            where workspace_id=? and workflow_key=? and status='active'
            """,
            (workspace_id, workflow_key),
        ).fetchone()
        if existing is not None:
            continue
        definition = load_workflow_definition(workflow_dir / f"{workflow_key}.yaml")
        definition_json = serialize_definition(definition)
        conn.execute(
            """
            insert into workflow_revisions(
              id, workspace_id, workflow_key, version, status,
              definition_json, definition_hash, published_at
            )
            values (?, ?, ?, 1, 'active', ?, ?, current_timestamp)
            """,
            (
                f"{workspace_id}:{workflow_key}:v1",
                workspace_id,
                workflow_key,
                definition_json,
                definition_hash(definition_json),
            ),
        )


MIGRATION = Migration(VERSION, NAME, apply)
