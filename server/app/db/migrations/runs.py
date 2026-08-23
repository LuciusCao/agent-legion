"""Runs cutover (schema v53, materials-and-runs design §5.2/§8, route A).

``job_batches`` becomes the first-class ``runs`` table with history kept:
rows move over with their ids unchanged, the payload's pin keys
(``node_code_versions`` / ``agent_versions`` / ``quality_replay``) land in
``runs.frozen_pins_json``, and the frozen ``node_config`` / ``task_candidates``
sink onto the batch's jobs (``jobs.frozen_config_json`` / ``jobs.input_json``,
RUN-FREEZE-001). Legacy candidates predate the materials model, so every
migrated input is the ``{"type": "ref", ..., "legacy": true}`` shape; a job
whose candidate cannot be matched by ``source_id`` keeps the minimal legacy
marker. Async intake runs (``_intake_queue`` in the payload) keep their whole
payload in ``runs.queue_payload_json`` so in-flight chunk consumption and the
depleted-requeue path survive the cutover untouched.

The data backfill runs as set-based SQL (one INSERT + two UPDATEs) instead of
per-row Python loops, so the single ``init_db`` startup transaction does not
hold ~260k per-row locks until commit; the statements live in the sibling
``runs_sql`` module (budget split).

Migration steps (idempotent, re-entrant):

1. ``jobs.batch_id`` → ``jobs.run_id`` (value unchanged), add nullable
   ``input_json`` / ``frozen_config_json``.
2. Create ``runs`` (also created by ``postgres_schema.sql``; repeated here so
   the function stands alone) and move every ``job_batches`` row over.
3. Sink the freeze columns onto jobs, then drop ``job_batches``.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.db.migrations.runs_sql import (
    _DECODE_FN_DDL,
    _INSERT_RUNS_SQL,
    _SINK_FROZEN_CONFIG_SQL,
    _SINK_INPUTS_SQL,
)

logger = logging.getLogger(__name__)

_RUNS_DDL = """
create table if not exists runs (
  id text primary key,
  workspace_id text not null references workspaces(id) on delete cascade,
  workflow_key text not null,
  source_kind text not null default '',
  status text not null default 'created',
  frozen_pins_json text not null default '{}',
  stats_json text not null default '{}',
  queue_payload_json text not null default '',
  created_count integer not null default 0,
  error_message text not null default '',
  created_by text not null default '',
  created_at timestamptz not null default current_timestamp,
  updated_at timestamptz not null default current_timestamp
)
"""


def migrate_runs(conn: Any) -> None:
    """Move ``job_batches`` onto ``runs`` + per-job freeze columns (v53)."""
    columns = {
        str(row["column_name"])
        for row in conn.execute(
            "select column_name from information_schema.columns"
            " where table_schema=current_schema() and table_name='jobs'"
        ).fetchall()
    }
    if "batch_id" in columns and "run_id" not in columns:
        conn.execute("alter table jobs rename column batch_id to run_id")
    conn.execute("alter table jobs add column if not exists input_json text")
    conn.execute("alter table jobs add column if not exists frozen_config_json text")
    conn.execute(_RUNS_DDL)
    conn.execute("create index if not exists idx_runs_workspace on runs(workspace_id, created_at)")
    conn.execute(
        "create index if not exists idx_runs_intake_queue"
        " on runs(status, updated_at) where status in ('queued', 'processing')"
    )
    if not conn.execute("select to_regclass('job_batches')").fetchone()["to_regclass"]:
        return
    conn.execute(_DECODE_FN_DDL)
    inserted = conn.execute(_INSERT_RUNS_SQL).rowcount
    conn.execute(_SINK_FROZEN_CONFIG_SQL)
    conn.execute(_SINK_INPUTS_SQL)
    conn.execute("drop table if exists job_batches")
    if inserted > 0:
        logger.info("runs cutover: migrated %d job_batches rows into runs", inserted)
