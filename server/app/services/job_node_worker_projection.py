"""Project Agent identity and claimed physical Worker onto job detail nodes."""

from __future__ import annotations

from typing import Any

from server.app.db.transaction import read_connection


def claimed_worker_map(connect_source: Any, job_id: str) -> dict[str, str]:
    """Map ``node_key`` to the Worker that claimed its active Agent execution.

    ``connect_source`` is the JobQueries facade (or a bare DSN, tests)
    — BOUNDARY-DATA-001, #187.
    """
    with read_connection(connect_source) as conn:
        rows = conn.execute(
            "select l.node_key as node_key, r.worker_id as worker_id"
            " from executor_leases l"
            " join agent_execution_requests r on r.execution_id = l.execution_id"
            " where l.job_id = %s and l.status = 'active'"
            " and r.state in ('claimed', 'reporting') and r.worker_id is not null"
            " order by l.acquired_at, l.id",
            (job_id,),
        ).fetchall()
    worker_map: dict[str, str] = {}
    for row in rows:
        worker_map.setdefault(str(row["node_key"]), str(row["worker_id"]))
    return worker_map


def node_route_rows(connect_source: Any, workspace_id: str) -> dict[str, dict[str, str]]:
    """node_key → current ``workspace_node_routes`` row (input of the shared
    route decision, ``services/node_route_decision``)."""
    with read_connection(connect_source) as conn:
        rows = conn.execute(
            "select node_key, target_kind, target_id from workspace_node_routes"
            " where workspace_id=%s",
            (workspace_id,),
        ).fetchall()
    return {
        str(row["node_key"]): {
            "target_kind": str(row["target_kind"]),
            "target_id": str(row["target_id"]),
        }
        for row in rows
    }
