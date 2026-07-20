"""Add the node_shards table for sharded workflow node fan-out."""

from __future__ import annotations

import sqlite3

from server.app.db.migrations.models import Migration

VERSION = 25
NAME = "node_shards"


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists node_shards (
            job_id text not null,
            node_key text not null,
            shard_index integer not null,
            status text not null default 'pending',
            input_json text not null default '{}',
            output_json text not null default '',
            error_message text not null default '',
            execution_id text not null default '',
            started_at text,
            finished_at text,
            primary key (job_id, node_key, shard_index),
            foreign key (job_id) references jobs(id) on delete cascade
        )
        """
    )


MIGRATION = Migration(VERSION, NAME, apply)
