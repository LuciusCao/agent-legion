"""Project Agent identity and claimed physical Worker onto job detail nodes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.db.transaction import read_connection
from server.app.executors.models import CODE_EXECUTOR_ID
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_node_profile import is_self_contained_agent_node


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


def agent_route_map(connect_source: Any, workspace_id: str, workflow_key: str) -> dict[str, str]:
    with read_connection(connect_source) as conn:
        rows = conn.execute(
            "select node_key, target_id from workspace_node_routes"
            " where workspace_id=%s and target_kind='agent'",
            (workspace_id,),
        ).fetchall()
    return {str(row["node_key"]): str(row["target_id"]) for row in rows}


def node_executor_projection(
    definition: WorkflowDefinition,
    node: Mapping[str, Any],
    agent_map: Mapping[str, str],
    worker_map: Mapping[str, str],
) -> dict[str, Any]:
    """Executor / Agent / Worker fields of one job-detail node.

    P-0.5: non-agent nodes always run on the implicit code pool. The job's
    frozen node type decides (#284) — a self-contained agent node (#933) has
    no Agent route row yet is never code; its requests carry agent_id = the
    node key.
    """
    node_key = str(node["node_key"])
    agent_id = agent_map.get(node_key)
    frozen = definition.nodes.get(node_key)
    if agent_id is None and frozen is not None and is_self_contained_agent_node(frozen):
        agent_id = node_key
    is_agent = agent_id is not None or (frozen is not None and frozen.node_type == "agent")
    return {
        "executor_id": None if is_agent else CODE_EXECUTOR_ID,
        "executor_kind": None if is_agent else "code",
        "worker_id": worker_map.get(node_key),
        "agent_id": agent_id,
    }
