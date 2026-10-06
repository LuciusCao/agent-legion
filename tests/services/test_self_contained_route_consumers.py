"""Route-less self-contained agent nodes are never classified as code (#933).

Self-contained agent nodes (``execution.runtime``) materialize no
``workspace_node_routes`` row by design (#440 P2). Every consumer that used
"no route" to mean "code node" must classify from the job's frozen node
definition instead (PR #1050 codex finding on #1039): job detail
projection, the quality replay pin resolver (explicit not-yet-supported
error), and the settings code-node set (frontend ``lib/codeNodes``).
"""

from __future__ import annotations

import pytest

from server.app.services.job_errors import InvalidOperationError
from server.app.services.job_queries import JobQueryService
from server.app.services.quality_replays import QualityReplayService
from server.app.services.workflow_revision_format import definition_hash, serialize_definition
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.workflows.definition import workflow_definition_from_mapping

_DEFINITION = {
    "label": "Self-contained",
    "execution": {"runtime": "velites", "provider": "p", "model": "m"},
    "nodes": {
        "fetch": {"type": "code", "capability": "fetch", "outputs": ["a.json"]},
        "draft": {
            "type": "agent",
            "capability": "draft",
            "inputs": ["a.json"],
            "outputs": ["b.json"],
            "after": ["fetch"],
        },
    },
}


def test_job_detail_projects_a_route_less_self_contained_node_as_agent(job_db, settings) -> None:
    workspace = job_db.create_workspace("Self-contained detail", workspace_id="sc_detail")
    definition = workflow_definition_from_mapping({"key": workspace["id"], **_DEFINITION})
    snapshot = serialize_definition(definition)
    job = job_db.create_job(
        workflow_key=workspace["id"],
        source_type="question",
        source_id="Q1",
        run_id="",
        title="Q1",
        node_keys=["fetch", "draft"],
        workspace_id=workspace["id"],
        workflow_definition_hash=definition_hash(snapshot),
        workflow_definition_snapshot_json=snapshot,
    )
    service = JobQueryService(job_db, settings, WorkspaceExecutionConfigurationService(job_db))

    nodes = {node["node_key"]: node for node in service.detail(job["id"])["nodes"]}

    assert nodes["fetch"]["executor_kind"] == "code"
    assert nodes["draft"]["executor_kind"] is None
    assert nodes["draft"]["executor_id"] is None
    # Requests of self-contained nodes carry agent_id = node key.
    assert nodes["draft"]["agent_id"] == "draft"


@pytest.mark.no_db
def test_quality_replay_rejects_self_contained_nodes_with_the_unsupported_reason() -> None:
    definition = workflow_definition_from_mapping({"key": "ws", **_DEFINITION})
    service = QualityReplayService(job_db=None, artifact_store=None)  # type: ignore[arg-type]

    with pytest.raises(InvalidOperationError, match="self-contained agent node") as raised:
        service._resolve_agent_pin(None, "ws", "ws", definition.nodes["draft"], None)

    assert "no workspace route" not in str(raised.value)


def _job(job_db, workspace_id: str, definition, job_id_source: str) -> dict:
    snapshot = serialize_definition(definition)
    return job_db.create_job(
        workflow_key=workspace_id,
        source_type="question",
        source_id=job_id_source,
        run_id="",
        title=job_id_source,
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace_id,
        workflow_definition_hash=definition_hash(snapshot),
        workflow_definition_snapshot_json=snapshot,
    )


def _add_route(job_db, workspace_id: str, node_key: str, agent_id: str) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
            " values (%s, %s, 'agent', %s)",
            (workspace_id, node_key, agent_id),
        )


def test_frozen_node_type_wins_over_routes_added_later(job_db, settings) -> None:
    """PR #1085 codex R1: routes are current state. A job frozen with
    ``fetch`` as code stays code after a later revision routes ``fetch`` to
    an Agent; a frozen self-contained node keeps its node-key identity after
    a route for the same node key appears."""
    workspace = job_db.create_workspace("Frozen wins", workspace_id="sc_frozen")
    definition = workflow_definition_from_mapping({"key": workspace["id"], **_DEFINITION})
    job = _job(job_db, workspace["id"], definition, "Q1")
    _add_route(job_db, workspace["id"], "fetch", "fetch-agent")
    _add_route(job_db, workspace["id"], "draft", "draft-agent")
    service = JobQueryService(job_db, settings, WorkspaceExecutionConfigurationService(job_db))

    nodes = {node["node_key"]: node for node in service.detail(job["id"])["nodes"]}

    assert nodes["fetch"]["executor_kind"] == "code"
    assert nodes["fetch"]["agent_id"] is None
    assert nodes["draft"]["executor_kind"] is None
    assert nodes["draft"]["agent_id"] == "draft"


def test_frozen_legacy_agent_node_still_reads_its_route(job_db, settings) -> None:
    workspace = job_db.create_workspace("Legacy agent", workspace_id="sc_legacy")
    raw = {"key": workspace["id"], **_DEFINITION, "execution": {"provider": "p", "model": "m"}}
    job = _job(job_db, workspace["id"], workflow_definition_from_mapping(raw), "Q1")
    _add_route(job_db, workspace["id"], "draft", "draft-agent")
    service = JobQueryService(job_db, settings, WorkspaceExecutionConfigurationService(job_db))

    nodes = {node["node_key"]: node for node in service.detail(job["id"])["nodes"]}

    assert nodes["draft"]["executor_kind"] is None
    assert nodes["draft"]["agent_id"] == "draft-agent"
