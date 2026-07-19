from __future__ import annotations

import sqlite3

from server.app.db.migrations.models import Migration

VERSION = 22
NAME = "job_event_seq_worker_control"


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists job_event_seq (
          id integer primary key check (id = 1),
          value integer not null
        )
        """
    )
    conn.execute("insert or ignore into job_event_seq (id, value) values (1, 0)")
    conn.execute(
        """
        create table if not exists worker_control_state (
          scope text primary key,
          paused integer not null default 1,
          updated_by text not null,
          updated_at text not null
        )
        """
    )


MIGRATION = Migration(VERSION, NAME, apply)
