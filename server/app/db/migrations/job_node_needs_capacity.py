"""Schema v105: static capacity metadata and approval-park candidate index."""

from __future__ import annotations

import json
from typing import Any

from server.app.workflows.capacity_metadata import node_capacity_requirements
from server.app.workflows.definition import (
    WorkflowDefinition,
    workflow_definition_from_dict,
)
from server.app.workflows.revision_format import definition_from_job_snapshot

_BATCH_JOBS = 500
_DDL = """
alter table job_nodes add column if not exists needs_capacity boolean not null default true;
"""
_INDEX_DDL = """
create index if not exists idx_job_nodes_approval_park_candidates
  on job_nodes(job_id, node_key)
  where needs_capacity = false and status in ('pending', 'ready', 'stale')
"""
_ACTIVE_REVISIONS = (
    "select workspace_id, definition_json from workflow_revisions where status='active'"
)
_JOB_BATCH = """
select j.id, j.workspace_id, j.workflow_definition_hash,
       j.workflow_definition_snapshot_json, array_agg(jn.node_key) as node_keys
from jobs j
join job_nodes jn on jn.job_id = j.id
where (j.workspace_id, j.workflow_definition_hash, j.id) > (%s, %s, %s)
group by j.id, j.workspace_id, j.workflow_definition_hash, j.workflow_definition_snapshot_json
order by j.workspace_id, j.workflow_definition_hash, j.id
limit %s
"""
_DONOR_SNAPSHOT = """
select workflow_definition_snapshot_json from jobs
where workflow_definition_hash=%s and workflow_definition_snapshot_json != ''
limit 1
"""
_UPDATE = """
update job_nodes as jn
set needs_capacity = v.needs_capacity
from unnest(%s::text[], %s::text[], %s::boolean[])
  as v(job_id, node_key, needs_capacity)
where jn.job_id = v.job_id and jn.node_key = v.node_key
  and jn.needs_capacity is distinct from v.needs_capacity
"""


def _active_definitions(conn: Any) -> dict[str, WorkflowDefinition]:
    definitions = {}
    for row in conn.execute(_ACTIVE_REVISIONS).fetchall():
        try:
            definitions[str(row["workspace_id"])] = workflow_definition_from_dict(
                json.loads(str(row["definition_json"]))
            )
        except (ValueError, TypeError):
            continue
    return definitions


def _definition_for_job(
    conn: Any,
    job: dict[str, Any],
    active: dict[str, WorkflowDefinition],
    donors: dict[str, str],
    parsed: dict[str, WorkflowDefinition | None],
) -> WorkflowDefinition | None:
    definition_hash = str(job["workflow_definition_hash"] or "")
    fallback = active.get(str(job["workspace_id"]))
    if not definition_hash:
        return fallback
    snapshot = str(job["workflow_definition_snapshot_json"] or "")
    if not snapshot:
        if definition_hash not in donors:
            row = conn.execute(_DONOR_SNAPSHOT, (definition_hash,)).fetchone()
            donors[definition_hash] = (
                str(row["workflow_definition_snapshot_json"]) if row is not None else ""
            )
        snapshot = donors[definition_hash]
    if not snapshot:
        return fallback
    if snapshot not in parsed:
        parsed[snapshot] = definition_from_job_snapshot(
            {"id": job["id"], "workflow_definition_snapshot_json": snapshot}
        )
    return parsed[snapshot] or fallback


def _backfill_batch(
    conn: Any, jobs: list[dict[str, Any]], active: dict[str, WorkflowDefinition]
) -> None:
    donors: dict[str, str] = {}
    parsed: dict[str, WorkflowDefinition | None] = {}
    updates: list[tuple[str, str, bool]] = []
    for job in jobs:
        definition = _definition_for_job(conn, job, active, donors, parsed)
        if definition is None:
            continue
        requirements = node_capacity_requirements(definition)
        updates.extend(
            (str(job["id"]), str(node_key), requirements.get(str(node_key), True))
            for node_key in job["node_keys"]
        )
    if updates:
        conn.execute(_UPDATE, [list(column) for column in zip(*updates, strict=True)])


def migrate_job_node_needs_capacity(conn: Any) -> None:
    """Add and backfill static metadata; legacy or unreadable rows stay true."""
    conn.execute(_DDL)
    active = _active_definitions(conn)
    cursor = ("", "", "")
    while True:
        jobs = [dict(row) for row in conn.execute(_JOB_BATCH, (*cursor, _BATCH_JOBS)).fetchall()]
        if not jobs:
            break
        _backfill_batch(conn, jobs, active)
        last = jobs[-1]
        cursor = (
            str(last["workspace_id"]),
            str(last["workflow_definition_hash"]),
            str(last["id"]),
        )
    conn.execute(_INDEX_DDL)
