from __future__ import annotations

import sqlite3

from server.app.db.migrations.models import Migration

VERSION = 18
NAME = "node_run_token_usage"


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists node_run_token_usage (
          id integer primary key autoincrement,
          node_run_id integer not null unique,
          job_id text not null,
          workspace_id text not null,
          node_key text not null,
          provider text not null default '',
          model text not null default '',
          skill_version text not null default '',
          message_count integer not null default 0,
          input_tokens integer not null default 0,
          output_tokens integer not null default 0,
          cache_read_tokens integer not null default 0,
          total_tokens integer not null default 0,
          usage_source text not null default 'events_jsonl',
          is_complete integer not null default 1 check(is_complete in (0, 1)),
          parse_error text not null default '',
          created_at text not null default current_timestamp,
          updated_at text not null default current_timestamp,
          foreign key(node_run_id) references node_runs(id) on delete cascade,
          foreign key(job_id) references jobs(id) on delete cascade,
          foreign key(workspace_id) references workspaces(id) on delete cascade
        )
        """
    )
    conn.execute(
        "create index if not exists idx_node_run_token_usage_workspace "
        "on node_run_token_usage(workspace_id, node_key)"
    )
    conn.execute(
        "create index if not exists idx_node_run_token_usage_model "
        "on node_run_token_usage(provider, model)"
    )
    conn.execute(
        "create index if not exists idx_node_run_token_usage_skill_version "
        "on node_run_token_usage(skill_version)"
    )


MIGRATION = Migration(VERSION, NAME, apply)
