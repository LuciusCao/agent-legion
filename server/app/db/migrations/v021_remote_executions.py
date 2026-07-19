from __future__ import annotations

import sqlite3

from server.app.db.migrations.models import Migration

VERSION = 21
NAME = "remote_executions"


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists remote_executions (
          execution_id text primary key,
          lease_id text not null,
          job_id text not null,
          node_key text not null,
          capability text not null,
          bundle_name text not null,
          manifest_json text not null,
          state text not null default 'queued',
          worker_id text,
          requeue_count integer not null default 0,
          last_heartbeat_at text,
          outcome_json text,
          created_at text not null,
          updated_at text not null
        )
        """
    )
    conn.execute(
        """
        create index if not exists idx_remote_executions_dequeue
        on remote_executions(state, capability) where state = 'queued'
        """
    )


MIGRATION = Migration(VERSION, NAME, apply)
