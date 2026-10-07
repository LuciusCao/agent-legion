"""Job detail agent projection follows the job snapshot's node type (#935 R1).

A frozen ``workspace_node_routes`` row may outlive a node the snapshot
declares ``code`` (route freeze keeps rows of agent nodes only, but rows
can predate the freeze); self-contained agent nodes have no route row at
all. Job detail must classify both by the snapshot, not by the route table.
"""

from __future__ import annotations

import json

import pytest

from server.app.services.job_queries import JobQueryService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from server.app.workflows.loader import workflow_definition_from_mapping
from server.app.workflows.revision_format import serialize_definition


@pytest.fixture
def query_service(job_db, settings):
    return JobQueryService(job_db, settings, WorkspaceExecutionConfigurationService(job_db))


def test_detail_classifies_nodes_by_snapshot_type_not_route_rows(query_service, job_db) -> None:
    workspace = job_db.create_workspace("detail-ws", workspace_id="detail_ws")
    definition = workflow_definition_from_mapping(
        {
            "key": "detail_ws",
            "label": "Detail",
            "nodes": {
                "turned_code": {"type": "code", "capability": "turned_code"},
                "inlined": {
                    "type": "agent",
                    "capability": "inlined",
                    "after": ["turned_code"],
                    "execution": {"runtime": "velites"},
                },
            },
        }
    )
    snapshot = serialize_definition(definition)
    run = job_db.create_run(
        "detail_ws", "batch_by_ids", {"question_ids": ["Q1"]}, workspace_id=workspace["id"]
    )
    job = job_db.create_job(
        workflow_key="detail_ws",
        source_type="question",
        source_id="Q1",
        run_id=run["id"],
        title="Q1",
        node_keys=["turned_code", "inlined"],
        workspace_id=workspace["id"],
        workflow_definition_snapshot_json=snapshot,
        workflow_definition_hash="h",
    )
    with job_db.connect() as conn:
        # A stale agent row for the node this snapshot declares code.
        conn.execute(
            "insert into workspace_node_routes(workspace_id, node_key, target_kind, target_id)"
            " values (%s, 'turned_code', 'agent', 'old-agent')",
            (workspace["id"],),
        )

    nodes = {node["node_key"]: node for node in query_service.detail(job["id"])["nodes"]}

    assert json.loads(snapshot)["nodes"]["turned_code"]["node_type"] == "code"
    assert nodes["turned_code"]["executor_kind"] == "code"
    assert nodes["turned_code"]["agent_id"] is None
    assert nodes["inlined"]["executor_kind"] is None
    assert nodes["inlined"]["agent_id"] == "inlined"
