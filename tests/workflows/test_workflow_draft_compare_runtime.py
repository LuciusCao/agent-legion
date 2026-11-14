"""Draft compare and publish agree on node-level runtime classification (#1114, #805)."""

from dataclasses import replace

import pytest
import yaml
from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.services.workflow_revision_change import revision_structurally_changed
from server.app.services.workflow_revision_format import definition_to_yaml
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.schema import (
    WorkflowDefinition,
    WorkflowIntake,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowReduceSpec,
    WorkflowShardSpec,
)
from tests.helpers import load_builtin_definition
from tests.helpers.auth import authenticate_client

_WORKSPACE = "education_video_problems_generation"
_AGENT_NODE = "write_script"


def _definition(node: WorkflowNode) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="w", label="w", intake=WorkflowIntake(), nodes={node.key: node}, schema_version=2
    )


def _agent_node(**execution: str) -> WorkflowNode:
    return WorkflowNode(
        key="demo",
        label="demo",
        capability="demo",
        node_type="agent",
        execution=WorkflowNodeExecution(runtime="velites", **execution),
    )


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("change", "structural"),
    [
        ({"execution": WorkflowNodeExecution(runtime="pi")}, True),
        ({"config": {"max_words": 800}}, True),
        ({"config_schema": {"type": "object", "properties": {}}}, True),
        ({"node_type": "code"}, True),
        ({"after": ["x"]}, True),
        ({"shard": WorkflowShardSpec(count=4)}, True),
        ({"reduce": WorkflowReduceSpec(from_node="x")}, True),
        ({"tools": ("bash",)}, False),
        ({"requires_labels": {"gpu": "true"}}, False),
        ({"execution": WorkflowNodeExecution(runtime="velites", provider="p")}, False),
        ({"execution": WorkflowNodeExecution(runtime="velites", model="m")}, False),
        ({"execution": WorkflowNodeExecution(runtime="velites", thinking="high")}, False),
        ({"execution": WorkflowNodeExecution(runtime="velites", prompt="hi")}, False),
        ({"execution": WorkflowNodeExecution(runtime="velites", prompt_mode="overwrite")}, False),
    ],
)
def test_revision_structurally_changed_decision_table(change, structural):
    base = _agent_node()
    draft = replace(base, **change)
    assert revision_structurally_changed(_definition(base), _definition(draft)) is structural


@pytest.fixture
def baseline(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    response = authenticate_client(TestClient(app)).post(
        "/api/workspaces", json={"id": _WORKSPACE, "name": "Studio"}
    )
    workspace_id = response.json()["workspace"]["id"]
    definition = load_builtin_definition(_WORKSPACE)
    WorkflowRevisionService(app.state.job_db).publish_workspace_revision(workspace_id, definition)
    return app, workspace_id, definition


def _node_runtime(raw: dict) -> None:
    raw["nodes"][_AGENT_NODE]["execution"] = {"runtime": "pi"}


def _node_execution(**values: str):
    def mutate(raw: dict) -> None:
        node = raw["nodes"][_AGENT_NODE]
        node["execution"] = {**node.get("execution", {}), **values}

    return mutate


def _top_level_execution(**values: str):
    def mutate(raw: dict) -> None:
        raw["execution"] = {"runtime": "velites", **values}

    return mutate


@pytest.mark.parametrize(
    ("mutate", "creates_revision", "runtime_nodes"),
    [
        pytest.param(_node_runtime, True, {_AGENT_NODE}, id="node-runtime"),
        pytest.param(_node_execution(provider="openai"), False, set(), id="node-provider"),
        pytest.param(_node_execution(model="gpt-x"), False, set(), id="node-model"),
        pytest.param(_node_execution(thinking="high"), False, set(), id="node-thinking"),
        pytest.param(_node_execution(prompt="be brief"), False, set(), id="node-prompt"),
        pytest.param(
            _node_execution(prompt="be brief", prompt_mode="overwrite"),
            False,
            set(),
            id="node-prompt-mode",
        ),
    ],
)
def test_compare_creates_revision_matches_publish_path(
    baseline, mutate, creates_revision, runtime_nodes
):
    app, workspace_id, definition = baseline
    raw = yaml.safe_load(definition_to_yaml(definition))
    mutate(raw)
    draft_yaml = yaml.safe_dump(raw, allow_unicode=True)

    with authenticate_client(TestClient(app)) as client:
        response = client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/compare",
            json={"definition_yaml": draft_yaml},
        )
    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is True
    assert result["creates_revision"] is creates_revision
    changed = {
        change["node_key"]
        for change in result["summary"]["node_changes"]
        if "runtime" in change["fields"]
    }
    assert changed == runtime_nodes
    if not creates_revision:
        node_changes = result["summary"]["node_changes"]
        assert node_changes
        assert all(change["fields"] == ["execution"] for change in node_changes)

    service = WorkflowRevisionService(app.state.job_db)
    before = service.get_active(workspace_id, definition.key)
    after = service.save_workspace_revision(
        workspace_id, workflow_definition_from_yaml_string(draft_yaml)
    )
    assert (int(after["version"]) != int(before["version"])) is creates_revision


def test_compare_rejects_retired_top_level_execution_without_side_effects(baseline) -> None:
    app, workspace_id, definition = baseline
    raw = yaml.safe_load(definition_to_yaml(definition))
    _top_level_execution(model="must-not-land")(raw)
    before = app.state.job_db.get_active_workflow_revision(workspace_id, workspace_id)

    with authenticate_client(TestClient(app)) as client:
        response = client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/compare",
            json={"definition_yaml": yaml.safe_dump(raw, allow_unicode=True)},
        )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False
    assert result["summary"] is None
    assert "#805" in result["errors"][0]["message"]
    assert f"nodes.{_AGENT_NODE}.execution" in result["errors"][0]["message"]
    after = app.state.job_db.get_active_workflow_revision(workspace_id, workspace_id)
    assert after == before
