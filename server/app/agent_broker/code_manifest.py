"""Kind='code' manifest size discipline (issue #142).

The queued manifest must never embed the heavy ``runtime_context`` payloads:
a full intake batch row is ~1.7MB, and ~112k code executions grew
``agent_execution_requests`` to ~198G of TOAST. This module owns the three
halves of the fix:

- ``runtime_context_stub``: the lightweight audit reference (job/workspace
  ids, batch_id + batch_hash) that IS persisted at enqueue;
- ``resolve_code_runtime_context``: the claim-response-path rebuild of the
  full runtime_context (job, workspace, settings_config, job_batch,
  skill_versions) — memory only, never persisted, mirroring the secret
  injection in ``resolve_code_manifest_config``;
- ``CODE_MANIFEST_TRIM``: the SQL fragment that slims terminal kind='code'
  rows back to the stub (legacy rows enqueued before the fix still carry the
  full context; every terminal transition applies it).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from server.app.config_schema import node_safe_settings_config
from server.app.db.transaction import read_connection

logger = logging.getLogger(__name__)

# SQL fragment: replace the heavy runtime_context of a kind='code' row with
# the lightweight audit stub. Applied on every terminal-state transition
# (broker mark_done, cancel_request, requeue-limit-exceeded, zombie-claim
# close, agent-disabled and unclaimable sweeps, rerun cancellation in
# jobs/atomic_mutations.py); the qualified table name keeps the correlated
# jobs lookup unambiguous inside each UPDATE.
CODE_MANIFEST_TRIM = """
case when kind = 'code' then
  jsonb_set(
    manifest_json::jsonb,
    '{runtime_context}',
    jsonb_build_object(
      'job_id', agent_execution_requests.job_id,
      'workspace_id', agent_execution_requests.workspace_id,
      'batch_id', coalesce(
        nullif(manifest_json::jsonb #>> '{runtime_context,batch_id}', ''),
        nullif(manifest_json::jsonb #>> '{runtime_context,job,batch_id}', ''),
        (select nullif(j.batch_id, '') from jobs j where j.id = agent_execution_requests.job_id)
      ),
      'batch_hash', nullif(manifest_json::jsonb #>> '{runtime_context,batch_hash}', '')
    )
  )::text
else manifest_json
end
"""


def _json_safe(value: Any) -> Any:
    """JSON round-trip so DB row dicts (datetimes) fit the manifest document."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _batch_hash(batch: dict[str, Any]) -> str:
    """Canonical sha256 of a batch row (audit reference, issue #142)."""
    return hashlib.sha256(
        json.dumps(batch, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def runtime_context_stub(
    job: dict[str, Any], workspace: dict[str, Any], batch: dict[str, Any] | None
) -> dict[str, Any]:
    """Lightweight persisted audit reference for kind='code' rows (issue #142).

    Only references persist — the full payloads are rebuilt on the
    claim-response path by ``resolve_code_runtime_context`` (memory only).
    """
    batch_id = str(job.get("batch_id") or "")
    batch_hash = ""
    if batch:
        batch_id = batch_id or str(batch.get("id") or "")
        batch_hash = _batch_hash(batch)
    return {
        "job_id": str(job.get("id") or ""),
        "workspace_id": str(workspace.get("id") or ""),
        "batch_id": batch_id or None,
        "batch_hash": batch_hash or None,
    }


def resolve_code_runtime_context(
    manifest: dict[str, Any],
    database_dsn: Any,
    settings_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Rebuild the full runtime_context at claim time (memory only).

    Issue #142: the queued manifest persists only the lightweight stub, so
    the claim response must re-fetch the DB-derived inputs (job, workspace,
    the intake batch row, skill_versions) before shipping the manifest to
    the Worker. Degradation mirrors the enqueue-time prefetch: a missing or
    unreadable batch degrades to None and skill_versions to {}; job and
    workspace rows are essential (node code reads ctx.job/ctx.workspace), so
    read failures propagate — the claim fails with 500 and the sweeper
    requeues, the same loop as secret-resolution failure.
    """
    job_id = str(manifest.get("job_id") or "")
    workspace_id = str(manifest.get("workspace_id") or "")
    job = _fetch_row(database_dsn, "select * from jobs where id=%s", job_id)
    workspace = _fetch_row(database_dsn, "select * from workspaces where id=%s", workspace_id)
    stub = manifest.get("runtime_context") or {}
    batch_id = str(stub.get("batch_id") or "")
    if not batch_id and job:
        batch_id = str(job.get("batch_id") or "")
    job_batch = None
    if batch_id:
        try:
            batch = _fetch_row(database_dsn, "select * from job_batches where id=%s", batch_id)
        except Exception:
            logger.debug("claim-time get_batch failed for batch %s", batch_id, exc_info=True)
            batch = None
        if batch is not None:
            job_batch = _json_safe(batch)
            recorded_hash = str(stub.get("batch_hash") or "")
            if recorded_hash and _batch_hash(job_batch) != recorded_hash:
                logger.warning(
                    "batch %s changed since enqueue (hash mismatch) for job %s", batch_id, job_id
                )
    return {
        **manifest,
        "runtime_context": {
            "job": _json_safe(dict(job)) if job else {},
            "workspace": _json_safe(dict(workspace)) if workspace else {},
            "settings_config": _json_safe(node_safe_settings_config(settings_config or {})),
            "job_batch": job_batch,
            "skill_versions": _claim_time_skill_versions(database_dsn, job_id),
        },
    }


def _fetch_row(database_dsn: Any, query: str, value: Any) -> dict[str, Any] | None:
    """One row fetch for the claim-time runtime_context rebuild."""
    with read_connection(database_dsn) as conn:
        row = conn.execute(query, (value,)).fetchone()
    return dict(row) if row else None


def _claim_time_skill_versions(database_dsn: Any, job_id: str) -> dict[str, str]:
    """Collect ``node_key -> skill_version`` from this job's node runs.

    Best-effort like the enqueue-time prefetch: a transient DB error
    degrades to an empty mapping instead of failing the claim response.
    """
    if not job_id:
        return {}
    try:
        with read_connection(database_dsn) as conn:
            rows = conn.execute(
                "select node_key, skill_version from node_runs where job_id=%s order by id",
                (job_id,),
            ).fetchall()
    except Exception:
        logger.debug("claim-time list_node_runs failed for job %s", job_id, exc_info=True)
        return {}
    return {
        str(row["node_key"]): str(row["skill_version"])
        for row in rows
        if row.get("node_key") and row.get("skill_version")
    }
