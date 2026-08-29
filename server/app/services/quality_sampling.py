"""Deterministic sampling of node_runs into quality sample batches (schema v28).

Candidates come from node_runs joined to jobs (workspace/workflow scope),
node_run_token_usage (actual provider/model), agent_execution_requests
(capability + agent definition hash from the manifest), and
versioned_entities (agent version reverse-lookup by definition hash).
Ordering by md5(seed || node_run_id) makes the sample a deterministic
function of (seed, candidate set), so a batch is reproducible.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.job_errors import NotFoundError

_CANDIDATE_QUERY = """
select
  node_runs.id as node_run_id,
  node_runs.job_id,
  node_runs.node_key,
  node_runs.status as run_status,
  node_runs.skill_version,
  node_runs.failure_category,
  node_runs.failure_detail,
  coalesce(tu.provider, '') as provider,
  coalesce(tu.model, '') as model,
  coalesce(req.capability, '') as capability,
  coalesce(req.agent_definition_hash, '') as agent_definition_hash,
  agent.version as agent_version
from node_runs
join jobs on jobs.id = node_runs.job_id
left join node_run_token_usage tu on tu.node_run_id = node_runs.id
left join lateral (
  select
    aer.agent_definition_hash,
    aer.manifest_json::jsonb ->> 'capability' as capability
  from agent_execution_requests aer
  where aer.node_run_id = node_runs.id
  order by aer.queued_at desc, aer.execution_id desc
  limit 1
) req on true
left join lateral (
  select ve.version
  from versioned_entities ve
  where ve.entity_type = 'agent'
    and ve.workspace_id = jobs.workspace_id
    and ve.definition_hash = req.agent_definition_hash
  order by ve.version desc
  limit 1
) agent on true
where {where_clause}
order by md5(%s || node_runs.id::text)
limit %s
"""

_INSERT_ITEM = """
insert into quality_sample_items(
  id, batch_id, node_run_id, job_id, node_key, capability, skill_version,
  agent_definition_hash, agent_version, provider, model, run_status,
  failure_category, failure_detail
) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (batch_id, node_run_id) do nothing
"""


class QualitySamplingService:
    """Deterministic sampling; the connect source is the JobQueries facade
    (or a bare DSN for tests) — BOUNDARY-DATA-001, #187."""

    def __init__(self, db_path: ConnectSource) -> None:
        # Named ``connect_source`` per the #187 convention: the attribute
        # holds the facade, not a path.
        self._connect_source = db_path

    def create_batch(
        self,
        workspace_id: str,
        *,
        name: str,
        sample_size: int,
        workflow_key: str = "",
        node_keys: list[str] | None = None,
        statuses: list[str] | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        seed: str | None = None,
        created_by: str = "",
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a batch and snapshot up to ``sample_size`` matching runs."""
        resolved_seed = seed or uuid.uuid4().hex
        clauses = ["jobs.workspace_id = %s"]
        params: list[Any] = [workspace_id]
        if workflow_key:
            clauses.append("jobs.workflow_key = %s")
            params.append(workflow_key)
        if node_keys:
            clauses.append("node_runs.node_key = any(%s)")
            params.append(list(node_keys))
        if statuses:
            clauses.append("node_runs.status = any(%s)")
            params.append(list(statuses))
        if since is not None:
            clauses.append("node_runs.started_at >= %s")
            params.append(since)
        if until is not None:
            clauses.append("node_runs.started_at < %s")
            params.append(until)
        query = _CANDIDATE_QUERY.format(where_clause=" and ".join(clauses))

        batch_id = uuid.uuid4().hex
        with write_transaction(self._connect_source) as conn:
            workspace = conn.execute(
                "select id from workspaces where id = %s", (workspace_id,)
            ).fetchone()
            if workspace is None:
                raise NotFoundError("Workspace not found")
            candidates = conn.execute(query, (*params, resolved_seed, sample_size)).fetchall()
            conn.execute(
                """
                insert into quality_sample_batches(
                  id, workspace_id, name, workflow_key, filters_json,
                  sample_size, seed, created_by
                ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    batch_id,
                    workspace_id,
                    name,
                    workflow_key,
                    Jsonb(filters or {}),
                    sample_size,
                    resolved_seed,
                    created_by,
                ),
            )
            conn.executemany(
                _INSERT_ITEM,
                [
                    (
                        uuid.uuid4().hex,
                        batch_id,
                        row["node_run_id"],
                        row["job_id"],
                        row["node_key"],
                        row["capability"],
                        row["skill_version"],
                        row["agent_definition_hash"],
                        row["agent_version"],
                        row["provider"],
                        row["model"],
                        row["run_status"],
                        row["failure_category"],
                        row["failure_detail"],
                    )
                    for row in candidates
                ],
            )
            batch = conn.execute(
                "select * from quality_sample_batches where id = %s", (batch_id,)
            ).fetchone()
        result = dict(batch) if batch is not None else {}
        result["sampled_count"] = len(candidates)
        return result

    def list_batches(self, workspace_id: str) -> list[dict[str, Any]]:
        with read_connection(self._connect_source) as conn:
            rows = conn.execute(
                """
                select * from quality_sample_batches
                where workspace_id = %s
                order by created_at desc, id desc
                """,
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_batch(self, workspace_id: str, batch_id: str) -> dict[str, Any]:
        with read_connection(self._connect_source) as conn:
            row = conn.execute(
                "select * from quality_sample_batches where id = %s and workspace_id = %s",
                (batch_id, workspace_id),
            ).fetchone()
        if row is None:
            raise NotFoundError("Sample batch not found")
        return dict(row)
