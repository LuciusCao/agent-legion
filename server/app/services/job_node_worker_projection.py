"""Project Agent identity and claimed physical Worker onto job detail nodes."""

from __future__ import annotations

from server.app.db.transaction import read_connection


def claimed_worker_map(db_path: str, job_id: str) -> dict[str, str]:
    """Map ``node_key`` to the Worker that claimed its active Agent execution."""
    with read_connection(db_path) as conn:
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


def agent_route_map(db_path: str, workspace_id: str, workflow_key: str) -> dict[str, str]:
    with read_connection(db_path) as conn:
        rows = conn.execute(
            "select node_key, target_id from workspace_node_routes"
            " where workspace_id=%s and workflow_key=%s and target_kind='agent'",
            (workspace_id, workflow_key),
        ).fetchall()
    return {str(row["node_key"]): str(row["target_id"]) for row in rows}
