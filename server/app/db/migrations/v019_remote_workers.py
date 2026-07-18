from __future__ import annotations

import sqlite3

from server.app.db.migrations.models import Migration

VERSION = 19
NAME = "remote_workers"


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists remote_workers (
          worker_id text primary key,
          name text not null default '',
          capabilities_json text not null,
          slots integer not null,
          registered_at text not null,
          last_seen_at text not null
        )
        """
    )


MIGRATION = Migration(VERSION, NAME, apply)
