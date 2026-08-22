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

Migration steps (idempotent, re-entrant):

1. ``jobs.batch_id`` → ``jobs.run_id`` (value unchanged), add nullable
   ``input_json`` / ``frozen_config_json``.
2. Create ``runs`` (also created by ``postgres_schema.sql``; repeated here so
   the function stands alone) and move every ``job_batches`` row over.
3. Sink the freeze columns onto jobs, then drop ``job_batches``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_PIN_KEYS = ("node_code_versions", "agent_versions", "quality_replay")

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


def _decode(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _legacy_input(source_id: str, candidate: dict[str, Any] | None) -> dict[str, Any]:
    """The pre-materials input shape: every legacy candidate is a ref."""
    input_doc: dict[str, Any] = {
        "type": "ref",
        "connection_key": "",
        "external_id": source_id,
        "legacy": True,
    }
    if candidate:
        for key in ("entity_type", "title", "stem"):
            value = candidate.get(key)
            if value not in (None, ""):
                input_doc[key] = str(value)
    return input_doc


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
    batches = conn.execute("select * from job_batches order by created_at, id").fetchall()
    for batch in batches:
        batch_id = str(batch["id"])
        payload = _decode(batch["source_payload_json"])
        pins = {key: payload[key] for key in _PIN_KEYS if key in payload}
        queue_payload = (
            str(batch["source_payload_json"])
            if isinstance(payload.get("_intake_queue"), dict)
            else ""
        )
        conn.execute(
            """
            insert into runs(
              id, workspace_id, workflow_key, source_kind, status, frozen_pins_json,
              queue_payload_json, created_count, error_message, created_at, updated_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict(id) do nothing
            """,
            (
                batch_id,
                str(batch["workspace_id"]),
                str(batch["workflow_key"]),
                str(batch["source_kind"]),
                str(batch["status"]),
                json.dumps(pins, ensure_ascii=False, sort_keys=True),
                queue_payload,
                int(batch["created_count"] or 0),
                str(batch["error_message"] or ""),
                batch["created_at"],
                batch["updated_at"],
            ),
        )
        node_config = payload.get("node_config")
        if isinstance(node_config, dict) and node_config:
            conn.execute(
                "update jobs set frozen_config_json=%s"
                " where run_id=%s and frozen_config_json is null",
                (json.dumps(node_config, ensure_ascii=False, sort_keys=True), batch_id),
            )
        candidates = payload.get("task_candidates")
        by_source: dict[str, dict[str, Any]] = {}
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("entity_id") is not None:
                    by_source[str(candidate["entity_id"])] = candidate
        jobs = conn.execute(
            "select id, source_id from jobs where run_id=%s and input_json is null",
            (batch_id,),
        ).fetchall()
        for job in jobs:
            source_id = str(job["source_id"])
            conn.execute(
                "update jobs set input_json=%s where id=%s",
                (
                    json.dumps(
                        _legacy_input(source_id, by_source.get(source_id)), ensure_ascii=False
                    ),
                    str(job["id"]),
                ),
            )
    conn.execute("drop table if exists job_batches")
    if batches:
        logger.info("runs cutover: migrated %d job_batches rows into runs", len(batches))
