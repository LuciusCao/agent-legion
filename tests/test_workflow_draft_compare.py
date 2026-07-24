from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.services.workflow_revision_format import definition_to_yaml
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import load_workflow_definition
from server.app.workflows.schema import WorkflowNodeExecution


@pytest.fixture
def app_with_workspace(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    response = TestClient(app).post(
        "/api/workspaces",
        json={"name": "Studio", "default_workflow_key": "question_comprehension_info"},
    )
    workspace_id = response.json()["workspace"]["id"]
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    WorkflowRevisionService(app.state.job_db).publish_workspace_revision(workspace_id, definition)
    return app, workspace_id


def _compare(client: TestClient, workspace_id: str, definition_yaml: str) -> dict:
    response = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/compare",
        json={"definition_yaml": definition_yaml},
    )
    assert response.status_code == 200
    return response.json()


def test_compare_no_op_draft_returns_none_risk(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    yaml_text = definition_to_yaml(definition)

    with TestClient(app) as client:
        result = _compare(client, workspace_id, yaml_text)

    assert result["valid"] is True
    assert result["creates_revision"] is False
    assert result["summary"]["risk_level"] == "none"
    assert result["summary"]["node_changes"] == []
    assert result["summary"]["edge_changes"] == []
    assert result["summary"]["intake_changes"] == []
    assert result["summary"]["risk_flags"] == []
    assert result["base_revision"]["workflow_key"] == "question_comprehension_info"
    assert result["draft_workflow"]["key"] == "question_comprehension_info"


def test_compare_node_added_returns_info_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    definition.nodes["extra_node"] = definition.nodes["fetch_questions"].__class__(
        key="extra_node",
        label="额外节点",
        capability="extra_capability",
        after=[],
        inputs=[],
        outputs=[],
        terminal=None,
    )
    raw = definition_to_yaml(definition)

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    assert result["creates_revision"] is True
    change = next(c for c in result["summary"]["node_changes"] if c["node_key"] == "extra_node")
    assert change["type"] == "added"
    assert change["risk"] == "info"
    assert change["label"] == "额外节点"
    assert any(flag["code"] == "node_added" for flag in result["summary"]["risk_flags"])


def test_compare_execution_only_change_does_not_create_revision(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    node = definition.nodes["fetch_questions"]
    definition.nodes["fetch_questions"] = replace(
        node,
        execution=WorkflowNodeExecution(provider="openai", model="gpt-5.2"),
    )

    with TestClient(app) as client:
        result = _compare(client, workspace_id, definition_to_yaml(definition))

    assert result["valid"] is True
    assert result["creates_revision"] is False
    change = next(
        c for c in result["summary"]["node_changes"] if c["node_key"] == "fetch_questions"
    )
    assert change["fields"] == ["execution"]


def test_compare_node_removed_returns_breaking_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    del definition.nodes["fetch_questions"]
    clean_node = definition.nodes["clean_and_parse"]
    definition.nodes["clean_and_parse"] = clean_node.__class__(
        key=clean_node.key,
        label=clean_node.label,
        capability=clean_node.capability,
        after=[],
        inputs=clean_node.inputs,
        outputs=clean_node.outputs,
        terminal=clean_node.terminal,
    )
    object.__setattr__(
        definition,
        "edges",
        [
            edge
            for edge in definition.edges
            if edge.source != "fetch_questions" and edge.target != "fetch_questions"
        ],
    )
    raw = definition_to_yaml(definition)

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    change = next(
        c for c in result["summary"]["node_changes"] if c["node_key"] == "fetch_questions"
    )
    assert change["type"] == "removed"
    assert change["risk"] == "breaking"
    flag = next(flag for flag in result["summary"]["risk_flags"] if flag["code"] == "node_removed")
    assert "fetch_questions" in flag["message"]
    assert result["summary"]["risk_level"] == "breaking"


def test_compare_capability_changed_returns_breaking_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    node = definition.nodes["fetch_questions"]
    definition.nodes["fetch_questions"] = node.__class__(
        key=node.key,
        label=node.label,
        capability="different_capability",
        after=node.after,
        inputs=node.inputs,
        outputs=node.outputs,
        terminal=node.terminal,
    )
    raw = definition_to_yaml(definition)

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    change = next(
        c for c in result["summary"]["node_changes"] if c["node_key"] == "fetch_questions"
    )
    assert change["type"] == "modified"
    assert change["risk"] == "breaking"
    assert "capability" in change["fields"]
    flag = next(
        flag for flag in result["summary"]["risk_flags"] if flag["code"] == "capability_changed"
    )
    assert "different_capability" in flag["message"]


def test_compare_output_removed_returns_breaking_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    node = definition.nodes["fetch_questions"]
    definition.nodes["fetch_questions"] = node.__class__(
        key=node.key,
        label=node.label,
        capability=node.capability,
        after=node.after,
        inputs=node.inputs,
        outputs=[],
        terminal=node.terminal,
    )
    raw = definition_to_yaml(definition)

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    change = next(
        c for c in result["summary"]["node_changes"] if c["node_key"] == "fetch_questions"
    )
    assert change["type"] == "modified"
    assert change["risk"] == "breaking"
    assert "outputs" in change["fields"]
    flag = next(
        flag for flag in result["summary"]["risk_flags"] if flag["code"] == "output_removed"
    )
    assert "questions.json" in flag["message"]


def test_compare_edge_condition_changed_returns_breaking_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    new_edges = []
    for edge in definition.edges:
        if (
            edge.source == "classify_comprehension_eligibility"
            and edge.target == "generate_key_info"
            and edge.condition is not None
            and edge.condition.equals is True
        ):
            new_edges.append(
                edge.__class__(
                    source=edge.source,
                    target=edge.target,
                    condition=edge.condition.__class__(
                        artifact=edge.condition.artifact,
                        path=edge.condition.path,
                        equals=False,
                    ),
                )
            )
        else:
            new_edges.append(edge)
    object.__setattr__(definition, "edges", new_edges)
    raw = definition_to_yaml(definition)

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    change = next(
        c
        for c in result["summary"]["edge_changes"]
        if c["source"] == "classify_comprehension_eligibility"
        and c["target"] == "generate_key_info"
    )
    assert change["type"] == "condition_changed"
    assert change["risk"] == "breaking"
    assert change["before_condition"] == "$.eligible == true"
    assert change["after_condition"] == "$.eligible == false"
    assert result["summary"]["risk_level"] == "breaking"


def test_compare_invalid_yaml_returns_errors_and_no_summary(app_with_workspace):
    app, workspace_id = app_with_workspace

    with TestClient(app) as client:
        result = _compare(client, workspace_id, "key: value\n  bad_indent: x")

    assert result["valid"] is False
    assert result["summary"] is None
    assert result["base_revision"] is None
    assert result["draft_workflow"] is None
    assert len(result["errors"]) >= 1
    error = result["errors"][0]
    assert error["category"] == "yaml"
    assert error["line"] is not None
    assert error["column"] is not None


def test_compare_missing_active_revision_returns_revision_error(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    workspace = app.state.job_db.create_workspace(
        "Empty",
        default_workflow_key="question_comprehension_info",
    )
    workspace_id = workspace["id"]
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    raw = definition_to_yaml(definition)

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is False
    assert result["summary"] is None
    assert len(result["errors"]) == 1
    assert result["errors"][0]["category"] == "revision"


def test_compare_rejects_draft_key_mismatch(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    raw = definition_to_yaml(definition).replace(
        "key: question_comprehension_info",
        "key: changed_workflow_key",
    )

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is False
    assert result["summary"] is None
    assert result["base_revision"] is None
    assert result["draft_workflow"] is None
    assert len(result["errors"]) == 1
    assert result["errors"][0]["category"] == "schema"
    assert "changed_workflow_key" in result["errors"][0]["message"]
    assert "question_comprehension_info" in result["errors"][0]["message"]


def test_compare_aggregate_risk_breaking_wins(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    # Add a node (info) and remove an output (breaking) before serializing.
    definition.nodes["extra_node"] = definition.nodes["fetch_questions"].__class__(
        key="extra_node",
        label="额外节点",
        capability="extra_capability",
        after=[],
        inputs=[],
        outputs=[],
        terminal=None,
    )
    node = definition.nodes["fetch_questions"]
    definition.nodes["fetch_questions"] = node.__class__(
        key=node.key,
        label=node.label,
        capability=node.capability,
        after=node.after,
        inputs=node.inputs,
        outputs=[],
        terminal=node.terminal,
    )
    raw = definition_to_yaml(definition)

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    assert result["summary"]["risk_level"] == "breaking"
    assert any(c["risk"] == "info" for c in result["summary"]["node_changes"])
    assert any(c["risk"] == "breaking" for c in result["summary"]["node_changes"])


def test_compare_workflow_label_changed_returns_info_metadata_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    object.__setattr__(definition, "label", f"{definition.label} v2")
    raw = definition_to_yaml(definition)

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    change = result["summary"]["metadata_changes"][0]
    assert change["field"] == "label"
    assert change["risk"] == "info"
    assert result["summary"]["risk_level"] == "info"


def test_compare_schema_version_changed_returns_breaking_metadata_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_workflow_definition(Path("config/workflows/question_comprehension_info.yaml"))
    raw = definition_to_yaml(definition).replace("schema_version: 2", "schema_version: 3")

    with TestClient(app) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    change = result["summary"]["metadata_changes"][0]
    assert change["field"] == "schema_version"
    assert change["risk"] == "breaking"
    assert result["summary"]["risk_level"] == "breaking"
