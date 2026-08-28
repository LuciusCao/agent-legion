"""Per-workspace sampling for the Host operations metrics service (schema v23).

Writes one sample row per active workspace per minute (``worker_id=''`` +
``workspace_id=X``) so the monitoring panel can scope queue depth, active
executions and token throughput to a single workspace; mirrors the
per-Worker row pattern — workspaces with no activity in the bucket simply
get no row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.services.ops_metrics.sampling import _EMPTY_TOKENS, _upsert_sample


def collect_workspace_samples(
    conn: DatabaseConnection, bucket_start: datetime, bucket_end: datetime
) -> dict[str, dict[str, Any]]:
    """Aggregate queue depth, claimed executions and tokens per workspace."""
    queued = {
        row["workspace_id"]: row["c"]
        for row in conn.execute(
            "select workspace_id, count(*) as c from agent_execution_requests"
            " where state='queued' group by workspace_id"
        ).fetchall()
    }
    active = {
        row["workspace_id"]: row["c"]
        for row in conn.execute(
            "select workspace_id, count(*) as c from agent_execution_requests"
            " where state='claimed' group by workspace_id"
        ).fetchall()
    }
    tokens = {
        row["workspace_id"]: row
        for row in conn.execute(
            """
            select workspace_id,
                   coalesce(sum(input_tokens), 0) as input_tokens,
                   coalesce(sum(output_tokens), 0) as output_tokens,
                   coalesce(sum(cache_read_tokens), 0) as cache_read_tokens,
                   coalesce(sum(total_tokens), 0) as total_tokens
            from node_run_token_usage
            where created_at >= %s and created_at < %s
            group by workspace_id
            """,
            (bucket_start, bucket_end),
        ).fetchall()
    }
    return {
        workspace_id: {
            "queued": int(queued.get(workspace_id, 0)),
            "active_executions": int(active.get(workspace_id, 0)),
            "tokens": tokens.get(workspace_id, _EMPTY_TOKENS),
        }
        for workspace_id in sorted(queued.keys() | active.keys() | tokens.keys())
    }


def upsert_workspace_samples(
    conn: DatabaseConnection, bucket_start: datetime, samples: dict[str, dict[str, Any]]
) -> None:
    """Persist per-workspace rows; ``online_workers`` stays 0 (fleet-level)."""
    for workspace_id, sample in samples.items():
        _upsert_sample(
            conn,
            bucket_start,
            "",
            online_workers=0,
            active_executions=sample["active_executions"],
            tokens=sample["tokens"],
            queued=sample["queued"],
            workspace_id=workspace_id,
        )
