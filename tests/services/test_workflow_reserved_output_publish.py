from __future__ import annotations

import json

import pytest

from server.app.jobs.queries import JobQueries
from server.app.services.node_codes import NodeCodeService
from server.app.services.workflow_draft_publish import (
    publish_workflow_draft,
    validate_workflow_draft_for_publish,
)
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import workflow_definition_from_mapping
from tests.postgres_support import TEST_DATABASE_URL


def _yaml(output: str) -> str:
    return f"""
key: test_publish_flow
label: Test Publish Flow
nodes:
  do_thing:
    type: code
    capability: do_thing
    outputs: [{output}]
"""


def _definition(output: str, model: str = ""):
    return workflow_definition_from_mapping(
        {
            "key": "test_publish_flow",
            "label": "Test Publish Flow",
            "nodes": {
                "do_thing": {
                    "type": "code",
                    "capability": "do_thing",
                    "outputs": [output],
                    "execution": {"model": model},
                }
            },
        }
    )


def _workspace(queries: JobQueries) -> dict:
    return queries.create_workspace("reserved-output-ws", workspace_id="test_publish_flow")


def _seed_node_code() -> None:
    codes = NodeCodeService(TEST_DATABASE_URL)
    codes.save_draft(
        "test_publish_flow",
        "test_publish_flow",
        "do_thing",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish("test_publish_flow", "test_publish_flow", "do_thing")


def test_draft_validate_and_publish_reject_reserved_output_without_state_change(
    job_db: JobQueries,
) -> None:
    queries = job_db
    workspace = _workspace(queries)
    _seed_node_code()
    assert publish_workflow_draft(queries, workspace["id"], _yaml("output.json")) == (True, [])
    before = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    reserved = _yaml("./result.json")
    queries.upsert_workspace_workflow_draft(workspace["id"], reserved)

    errors = validate_workflow_draft_for_publish(queries, workspace["id"], reserved, True)
    ok, publish_errors = publish_workflow_draft(queries, workspace["id"], reserved)

    assert ok is False
    assert publish_errors == errors
    assert any("node do_thing" in error and "reserved result-archive" in error for error in errors)
    after = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert after["id"] == before["id"]
    assert after["definition_hash"] == before["definition_hash"]
    assert "result.json" not in str(after["definition_json"])
    draft = queries.get_workspace_workflow_draft(workspace["id"])
    assert draft["definition_yaml"] == reserved


@pytest.mark.parametrize("method", ["publish_workspace_revision", "ensure_active_revision"])
def test_revision_write_entries_fail_before_materializing_reserved_output(
    job_db: JobQueries, method: str
) -> None:
    queries = job_db
    workspace = _workspace(queries)
    service = WorkflowRevisionService(queries)

    with pytest.raises(ValueError, match="node do_thing.*reserved result-archive"):
        getattr(service, method)(workspace["id"], _definition("node.log"))

    assert queries.get_active_workflow_revision(workspace["id"], "test_publish_flow") is None


def test_runtime_only_save_rejects_reserved_output_without_mutating_active_revision(
    job_db: JobQueries,
) -> None:
    queries = job_db
    workspace = _workspace(queries)
    service = WorkflowRevisionService(queries)
    original = service.publish_workspace_revision(workspace["id"], _definition("output.json"))

    with pytest.raises(ValueError, match="reserved result-archive"):
        service.save_workspace_revision(
            workspace["id"], _definition("result-output-artifacts.json", model="new-model")
        )

    active = queries.get_active_workflow_revision(workspace["id"], "test_publish_flow")
    assert active["id"] == original["id"]
    assert active["definition_hash"] == original["definition_hash"]
    assert "new-model" not in str(active["definition_json"])
    assert queries.list_runtime_config_versions(workspace["id"]) == []
    assert json.loads(active["definition_json"])["nodes"]["do_thing"]["outputs"] == ["output.json"]
