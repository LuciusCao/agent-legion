"""Add content-addressed artifact tables and per-worker trust columns."""

from __future__ import annotations

import sqlite3

from server.app.db.migrations.models import Migration

VERSION = 24
NAME = "artifacts"


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists artifacts (
            hash text primary key,
            size integer not null,
            created_at text not null default current_timestamp
        )
        """
    )
    conn.execute(
        """
        create table if not exists artifact_refs (
            job_id text not null,
            node_key text not null,
            name text not null,
            hash text not null,
            primary key (job_id, node_key, name),
            foreign key (job_id) references jobs(id) on delete cascade
        )
        """
    )
    conn.execute("create index if not exists idx_artifact_refs_hash on artifact_refs(hash)")
    worker_columns = {row[1] for row in conn.execute("pragma table_info(remote_workers)")}
    if "token_hash" not in worker_columns:
        conn.execute("alter table remote_workers add column token_hash text not null default ''")
    if "labels_json" not in worker_columns:
        conn.execute("alter table remote_workers add column labels_json text not null default '{}'")
    if "revoked_at" not in worker_columns:
        conn.execute("alter table remote_workers add column revoked_at text")


MIGRATION = Migration(VERSION, NAME, apply)
