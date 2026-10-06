"""Job detail == dispatch, node by node, over every decision branch (PR #1085).

Each scenario seeds the route rows and published Agents, then compares the
workflow worker's ``resolve_node_route`` with the job-detail projection for
every node of the job's frozen definition.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from server.app.agent_catalog import AgentDefinition
from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.workflow_worker.routing import resolve_node_route
from server.app.workflows.definition import workflow_definition_from_mapping
from tests.helpers import replace_agent_catalog

_NODES = {
    # self-contained (node runtime) + a route for the same key added later
    "contained": {"type": "agent", "capability": "c", "execution": {"runtime": "velites"}},
    # legacy agent routed to a published Agent
    "routed": {"type": "agent", "capability": "r", "after": ["contained"]},
    # legacy agent routed to an unpublished Agent → error
    "dangling": {"type": "agent", "capability": "d", "after": ["contained"]},
    # legacy agent with capability mismatch on its route → error
    "mismatch": {"type": "agent", "capability": "m", "after": ["contained"]},
    # route-less legacy agent → capability fallback
    "fallback": {"type": "agent", "capability": "f", "after": ["contained"]},
    # route-less legacy agent without a published Agent → error
    "orphan": {"type": "agent", "capability": "o", "after": ["contained"]},
    # frozen code later routed to an Agent (#1091 gap) → follows the route
    "code_routed": {"type": "code", "capability": "x", "after": ["contained"]},
    # plain code → code pool
    "code": {"type": "code", "capability": "y", "after": ["contained"]},
}
_ROUTES = {
    "contained": "c-agent",
    "routed": "r-agent",
    "dangling": "ghost",
    "mismatch": "r-agent",
    "code_routed": "x-agent",
}
_CATALOG = {
    "c-agent": AgentDefinition(capability="c", runtime="pi"),
    "r-agent": AgentDefinition(capability="r", runtime="pi"),
    "f-agent": AgentDefinition(capability="f", runtime="pi"),
    "x-agent": AgentDefinition(capability="x", runtime="pi"),
}


def test_job_detail_equals_dispatch_for_every_branch(job_db, settings) -> None:
    workspace_id = str(job_db.create_workspace("Consistency", workspace_id="route_eq")["id"])
    definition = workflow_definition_from_mapping(
        {
            "key": workspace_id,
            "label": "Consistency",
            "execution": {"provider": "p", "model": "m"},
            "nodes": {key: {**raw, "outputs": [f"{key}.json"]} for key, raw in _NODES.items()},
        }
    )
    snapshot = serialize_definition(definition)
    job = job_db.create_job(
        workflow_key=workspace_id,
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace_id,
        workflow_definition_hash=definition_hash(snapshot),
        workflow_definition_snapshot_json=snapshot,
    )
    replace_agent_catalog(workspace_id, _CATALOG)
    with job_db.connect() as conn:
        for node_key, agent_id in _ROUTES.items():
            conn.execute(
                "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
                " values (%s, %s, 'agent', %s)",
                (workspace_id, node_key, agent_id),
            )
    worker = MagicMock()
    worker.job_db = job_db
    worker.state.route_cache = {}
    service = JobQueryService(job_db, settings, WorkspaceExecutionConfigurationService(job_db))

    detail = {node["node_key"]: node for node in service.detail(job["id"])["nodes"]}

    seen: dict[str, str] = {}
    for key, node in definition.executable_nodes.items():
        route = resolve_node_route(worker, workspace_id, workspace_id, node)
        projected = detail[key]
        seen[key] = route.kind
        if route.kind == "agent":
            assert projected["executor_kind"] is None, key
            assert projected["agent_id"] == route.target_id, key
            assert projected["route_error"] is None, key
        elif route.kind == "executor":
            assert projected["executor_kind"] == "code", key
            assert projected["agent_id"] is None, key
        else:
            assert projected["executor_kind"] is None, key
            assert projected["agent_id"] is None, key
            assert projected["route_error"] == route.error_message, key
    assert seen == {
        "contained": "agent",
        "routed": "agent",
        "dangling": "error",
        "mismatch": "error",
        "fallback": "agent",
        "orphan": "error",
        "code_routed": "agent",
        "code": "executor",
    }
    assert detail["contained"]["agent_id"] == "contained"
    assert detail["fallback"]["agent_id"] == "f-agent"
