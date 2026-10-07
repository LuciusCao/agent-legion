"""Dispatch routing and job detail follow the job's frozen node type (#1091).

An in-flight job executes its frozen snapshot: a later revision that turns
``write_script`` code → legacy agent (materializing a ``workspace_node_routes``
row) must not pull the old job's code node onto an Agent, and a revision
that turns it agent → code (pruning the row) must not drop the old job's
legacy agent node into the code pool. Job detail / MCP's executor projection
uses the same frozen-type decision, so both surfaces agree per (job, node).
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.workflow_worker.routing import resolve_node_route
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.revision_format import serialize_definition
from tests.helpers import load_builtin_definition, seed_workspace_agent_definitions
from tests.helpers.node_profile import legacy_profile_variant

NODE = "write_script"
AGENT = "example-write-script-v1"


@pytest.fixture
def workspace_id(job_db) -> str:
    workspace = job_db.create_workspace("frozen-type-ws", workspace_id="frozen_type_ws")
    seed_workspace_agent_definitions(workspace["id"])
    return str(workspace["id"])


@pytest.fixture
def query_service(job_db, settings) -> JobQueryService:
    return JobQueryService(job_db, settings, WorkspaceExecutionConfigurationService(job_db))


def _legacy() -> WorkflowDefinition:
    return legacy_profile_variant(load_builtin_definition("education_video_problems_generation"))


def _with_code_node(definition: WorkflowDefinition) -> WorkflowDefinition:
    node = replace(definition.nodes[NODE], node_type="code")
    return replace(definition, nodes={**definition.nodes, NODE: node})


def _job(job_db, workspace_id: str, definition: WorkflowDefinition, source_id: str) -> dict:
    run = job_db.create_run(
        definition.key, "batch_by_ids", {"question_ids": [source_id]}, workspace_id=workspace_id
    )
    return job_db.create_job(
        workflow_key=definition.key,
        source_type="question",
        source_id=source_id,
        run_id=run["id"],
        title=source_id,
        node_keys=list(definition.nodes),
        workspace_id=workspace_id,
        workflow_definition_snapshot_json=serialize_definition(definition),
        workflow_definition_hash=f"h-{source_id}",
    )


def _worker(job_db) -> SimpleNamespace:
    return SimpleNamespace(
        job_db=job_db, state=SimpleNamespace(route_cache={}), agent_dispatch=object()
    )


def _route_row(job_db, workspace_id: str) -> dict | None:
    with job_db.connect() as conn:
        return conn.execute(
            "select target_kind, target_id from workspace_node_routes"
            " where workspace_id=%s and node_key=%s",
            (workspace_id, NODE),
        ).fetchone()


def _dispatch(worker, workspace_id: str, definition: WorkflowDefinition) -> tuple[str, str]:
    route = resolve_node_route(worker, workspace_id, definition.key, definition.nodes[NODE])
    return route.kind, route.target_id


def _detail(query_service: JobQueryService, job: dict) -> dict:
    nodes = {node["node_key"]: node for node in query_service.detail(job["id"])["nodes"]}
    return nodes[NODE]


def _assert_consistent(dispatched: tuple[str, str], detail: dict) -> None:
    """Job detail shows exactly what dispatch decided for the same frozen node."""
    kind, target = dispatched
    if kind == "agent":
        assert (detail["executor_kind"], detail["agent_id"]) == (None, target)
    else:
        assert (detail["executor_kind"], detail["agent_id"]) == ("code", None)


def test_code_turned_agent_keeps_the_old_job_on_the_code_pool(
    job_db, workspace_id, query_service
) -> None:
    old_snapshot = _with_code_node(_legacy())
    old_job = _job(job_db, workspace_id, old_snapshot, "Q-old")
    # The next revision turns the node into a legacy agent node: publish
    # materializes its route row (only legacy agent nodes still do).
    new_snapshot = _legacy()
    WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, new_snapshot)
    assert _route_row(job_db, workspace_id) == {"target_kind": "agent", "target_id": AGENT}
    new_job = _job(job_db, workspace_id, new_snapshot, "Q-new")
    worker = _worker(job_db)

    # Both orders through one worker cache: neither job's frozen type leaks.
    old_route = _dispatch(worker, workspace_id, old_snapshot)
    new_route = _dispatch(worker, workspace_id, new_snapshot)
    assert _dispatch(worker, workspace_id, old_snapshot) == old_route

    assert old_route == ("executor", "code")
    assert new_route == ("agent", AGENT)
    _assert_consistent(old_route, _detail(query_service, old_job))
    _assert_consistent(new_route, _detail(query_service, new_job))


@pytest.mark.parametrize("route_pruned", [True, False], ids=["route-pruned", "route-frozen"])
def test_agent_turned_code_keeps_the_old_job_on_its_agent(
    job_db, workspace_id, query_service, route_pruned: bool
) -> None:
    old_snapshot = _legacy()
    WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, old_snapshot)
    old_job = _job(job_db, workspace_id, old_snapshot, "Q-old")
    if route_pruned:
        # The code revision prunes the row of the node it turned code
        # (prune_frozen_agent_routes); the old snapshot still says agent.
        with job_db.connect() as conn:
            conn.execute(
                "delete from workspace_node_routes where workspace_id=%s and node_key=%s",
                (workspace_id, NODE),
            )
    new_snapshot = _with_code_node(_legacy())
    new_job = _job(job_db, workspace_id, new_snapshot, "Q-new")
    worker = _worker(job_db)

    old_route = _dispatch(worker, workspace_id, old_snapshot)
    new_route = _dispatch(worker, workspace_id, new_snapshot)

    # Pruned: the capability fallback resolves the same published Agent.
    assert old_route == ("agent", AGENT)
    assert new_route == ("executor", "code")
    _assert_consistent(old_route, _detail(query_service, old_job))
    _assert_consistent(new_route, _detail(query_service, new_job))


def test_self_contained_snapshot_routes_to_its_own_profile(
    job_db, workspace_id, query_service
) -> None:
    WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, _legacy())
    snapshot = load_builtin_definition("education_video_problems_generation")
    assert snapshot.nodes[NODE].execution.runtime, "the demo ships self-contained nodes"
    job = _job(job_db, workspace_id, snapshot, "Q-inline")

    route = _dispatch(_worker(job_db), workspace_id, snapshot)

    # The frozen route row targets the Agent; the self-contained snapshot wins.
    assert route == ("agent", NODE)
    _assert_consistent(route, _detail(query_service, job))
