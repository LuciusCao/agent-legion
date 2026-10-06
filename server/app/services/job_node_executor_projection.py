"""Executor-kind projection of job-detail nodes (#284, #933, PR #1085).

Split from ``job_node_worker_projection`` (file budget). There is no
detail-side classification here: every node goes through the same pure
decision dispatch uses (``services/node_route_decision.decide_node_route``)
with the job's own node definition, the node key's current route row and
the workspace's published Agent catalog — so the detail shows what dispatch
actually does (#1091 flips both at once).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from server.app.executors.models import CODE_EXECUTOR_ID
from server.app.services.agent_node_profile_catalog import lazy_legacy_agent_catalog
from server.app.services.job_node_worker_projection import claimed_worker_map, node_route_rows
from server.app.services.node_route_decision import CatalogLoader, decide_node_route
from server.app.workflows.definition import WorkflowDefinition


def node_executor_projection(
    definition: WorkflowDefinition,
    node: Mapping[str, Any],
    route_rows: Mapping[str, Mapping[str, Any]],
    worker_map: Mapping[str, str],
    *,
    workspace_id: str,
    catalog: CatalogLoader,
) -> dict[str, Any]:
    """Executor / Agent / Worker fields of one job-detail node.

    ``agent`` decisions project ``agent_id`` (the request identity: the
    Agent id, or the node key for a self-contained node); ``executor`` is
    the code pool; an ``error`` decision (dispatch would fail the node as a
    configuration error) shows neither, with the reason in ``route_error``.
    A node missing from *definition* (definition drift) is projected from
    its route row alone, as before.
    """
    node_key = str(node["node_key"])
    worker_id = worker_map.get(node_key)
    target = definition.nodes.get(node_key)
    row = route_rows.get(node_key)
    if target is None:
        # Same predicate as the decision's routed branch: only an ``agent``
        # row is an Agent; any other row (e.g. a handler_executor binding)
        # projects to the implicit code pool.
        is_agent_row = row is not None and row.get("target_kind") == "agent"
        agent_id = str(row["target_id"]) if is_agent_row and row is not None else None
        return _fields(agent_id is not None, agent_id, worker_id, None)
    decision = decide_node_route(target, row, workspace_id=workspace_id, catalog=catalog)
    if decision.kind == "error":
        return _fields(True, None, worker_id, decision.error_message)
    is_agent = decision.kind == "agent"
    return _fields(is_agent, decision.target_id if is_agent else None, worker_id, None)


def _fields(
    is_agent: bool, agent_id: str | None, worker_id: str | None, route_error: str | None
) -> dict[str, Any]:
    return {
        "executor_id": None if is_agent else CODE_EXECUTOR_ID,
        "executor_kind": None if is_agent else "code",
        "worker_id": worker_id,
        "agent_id": agent_id,
        "route_error": route_error,
    }


def node_executor_projector(
    connect_source: Any, job: Mapping[str, Any], definition: WorkflowDefinition
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Per-job projector: reads route rows / claimed Workers once, the Agent
    catalog lazily (only if some node's decision needs it)."""
    workspace_id = str(job["workspace_id"])
    route_rows = node_route_rows(connect_source, workspace_id)
    worker_map = claimed_worker_map(connect_source, str(job["id"]))
    catalog = lazy_legacy_agent_catalog(connect_source, workspace_id)
    return lambda node: node_executor_projection(
        definition, node, route_rows, worker_map, workspace_id=workspace_id, catalog=catalog
    )
