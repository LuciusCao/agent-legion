from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.services.workflow_draft_compare import compare_workflow_draft
from server.app.services.workflow_revision_format import definition_to_yaml
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.definition import WorkflowCondition
from server.app.workflows.schema import WorkflowNodeExecution
from tests.helpers import load_builtin_definition
from tests.helpers.auth import authenticate_client


@pytest.fixture
def app_with_workspace(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    response = authenticate_client(TestClient(app)).post(
        "/api/workspaces",
        json={"id": "education_video_problems_generation", "name": "Studio"},
    )
    workspace_id = response.json()["workspace"]["id"]
    definition = load_builtin_definition("education_video_problems_generation")
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
    definition = load_builtin_definition("education_video_problems_generation")
    yaml_text = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, yaml_text)

    assert result["valid"] is True
    assert result["creates_revision"] is False
    assert result["summary"]["risk_level"] == "none"
    assert result["summary"]["node_changes"] == []
    assert result["summary"]["edge_changes"] == []
    assert result["summary"]["intake_changes"] == []
    assert result["summary"]["risk_flags"] == []
    assert result["base_revision"]["workflow_key"] == "education_video_problems_generation"
    assert result["draft_workflow"]["key"] == "education_video_problems_generation"


def test_compare_node_added_returns_info_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_builtin_definition("education_video_problems_generation")
    definition.nodes["extra_node"] = definition.nodes["intake_knowledge_points"].__class__(
        key="extra_node",
        label="额外节点",
        capability="extra_capability",
        after=[],
        inputs=[],
        outputs=[],
        terminal=None,
    )
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
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
    definition = load_builtin_definition("education_video_problems_generation")
    node = definition.nodes["write_script"]
    definition.nodes["write_script"] = replace(
        node,
        execution=WorkflowNodeExecution(provider="openai", model="gpt-5.2"),
    )

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, definition_to_yaml(definition))

    assert result["valid"] is True
    assert result["creates_revision"] is False
    change = next(c for c in result["summary"]["node_changes"] if c["node_key"] == "write_script")
    assert change["fields"] == ["execution"]


def test_compare_node_removed_returns_breaking_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_builtin_definition("education_video_problems_generation")
    del definition.nodes["review_questions"]
    publish_node = definition.nodes["publish_content"]
    definition.nodes["publish_content"] = publish_node.__class__(
        key=publish_node.key,
        label=publish_node.label,
        capability=publish_node.capability,
        after=["review_script"],
        inputs=publish_node.inputs,
        outputs=publish_node.outputs,
        terminal=publish_node.terminal,
    )
    object.__setattr__(
        definition,
        "edges",
        [
            edge
            for edge in definition.edges
            if edge.source != "review_questions" and edge.target != "review_questions"
        ],
    )
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    change = next(
        c for c in result["summary"]["node_changes"] if c["node_key"] == "review_questions"
    )
    assert change["type"] == "removed"
    assert change["risk"] == "breaking"
    flag = next(flag for flag in result["summary"]["risk_flags"] if flag["code"] == "node_removed")
    assert "review_questions" in flag["message"]
    assert result["summary"]["risk_level"] == "breaking"


def test_compare_capability_changed_returns_breaking_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_builtin_definition("education_video_problems_generation")
    node = definition.nodes["intake_knowledge_points"]
    definition.nodes["intake_knowledge_points"] = node.__class__(
        key=node.key,
        label=node.label,
        capability="different_capability",
        after=node.after,
        inputs=node.inputs,
        outputs=node.outputs,
        terminal=node.terminal,
    )
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    change = next(
        c for c in result["summary"]["node_changes"] if c["node_key"] == "intake_knowledge_points"
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
    definition = load_builtin_definition("education_video_problems_generation")
    node = definition.nodes["intake_knowledge_points"]
    definition.nodes["intake_knowledge_points"] = node.__class__(
        key=node.key,
        label=node.label,
        capability=node.capability,
        after=node.after,
        inputs=node.inputs,
        outputs=[],
        terminal=node.terminal,
    )
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    change = next(
        c for c in result["summary"]["node_changes"] if c["node_key"] == "intake_knowledge_points"
    )
    assert change["type"] == "modified"
    assert change["risk"] == "breaking"
    assert "outputs" in change["fields"]
    flag = next(
        flag for flag in result["summary"]["risk_flags"] if flag["code"] == "output_removed"
    )
    assert "knowledge_point.json" in flag["message"]


def test_compare_edge_condition_changed_returns_breaking_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_builtin_definition("education_video_problems_generation")
    new_edges = []
    for edge in definition.edges:
        if edge.source == "write_script" and edge.target == "review_script":
            new_edges.append(
                edge.__class__(
                    source=edge.source,
                    target=edge.target,
                    condition=WorkflowCondition(
                        artifact="script.md",
                        path="$.approved",
                        equals=True,
                    ),
                )
            )
        else:
            new_edges.append(edge)
    object.__setattr__(definition, "edges", new_edges)
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    change = next(
        c
        for c in result["summary"]["edge_changes"]
        if c["source"] == "write_script" and c["target"] == "review_script"
    )
    assert change["type"] == "condition_changed"
    assert change["risk"] == "breaking"
    assert change["before_condition"] == ""
    assert change["after_condition"] == "$.approved == true"
    assert result["summary"]["risk_level"] == "breaking"


def test_compare_invalid_yaml_returns_errors_and_no_summary(app_with_workspace):
    app, workspace_id = app_with_workspace

    with authenticate_client(TestClient(app)) as client:
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
        default_workflow_key="education_video_problems_generation",
    )
    workspace_id = workspace["id"]
    definition = load_builtin_definition("education_video_problems_generation")
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is False
    assert result["summary"] is None
    assert len(result["errors"]) == 1
    assert result["errors"][0]["category"] == "revision"


def test_compare_allow_missing_baseline_previews_full_draft(tmp_path):
    """allow_missing_baseline=True (studio-agent tool surface): a never-published
    workflow diffs against an empty base — every node/edge/intake field shows
    as added and a no_baseline flag explains the preview mode."""
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    workspace = app.state.job_db.create_workspace(
        "Empty",
        default_workflow_key="education_video_problems_generation",
    )
    definition = load_builtin_definition("education_video_problems_generation")
    raw = definition_to_yaml(definition)

    result = compare_workflow_draft(
        app.state.job_db,
        workspace["id"],
        raw,
        allow_missing_baseline=True,
    )

    assert result["valid"] is True
    assert result["errors"] == []
    assert result["base_revision"] is None
    assert result["draft_workflow"] == {
        "key": definition.key,
        "label": definition.label,
        "version": 0,
    }
    assert result["creates_revision"] is True
    node_changes = result["summary"]["node_changes"]
    assert len(node_changes) == len(definition.nodes)
    assert all(change["type"] == "added" for change in node_changes)
    edge_changes = result["summary"]["edge_changes"]
    assert len(edge_changes) == len(definition.edges)
    assert all(change["type"] == "added" for change in edge_changes)
    assert any(
        flag["code"] == "no_baseline" and flag["severity"] == "info"
        for flag in result["summary"]["risk_flags"]
    )


def test_compare_node_changes_carry_node_type(tmp_path):
    """A draft without a start node gets the loader-injected synthetic
    ``_start``; its added change is marked node_type 'start' so the canvas can
    synthesize the ghost node's inspector details."""
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    workspace = app.state.job_db.create_workspace("Empty", default_workflow_key="demo")
    raw = (
        "key: demo\n"
        "label: Demo\n"
        "schema_version: 2\n"
        "nodes:\n"
        "  fetch:\n"
        "    label: 拉取\n"
        "    capability: fetch\n"
        "  report:\n"
        "    label: 汇总\n"
        "    capability: report\n"
        "edges:\n"
        "  - {from: fetch, to: report}\n"
    )

    result = compare_workflow_draft(
        app.state.job_db, workspace["id"], raw, allow_missing_baseline=True
    )

    assert result["valid"] is True
    changes = {change["node_key"]: change for change in result["summary"]["node_changes"]}
    assert changes["_start"]["type"] == "added"
    assert changes["_start"]["node_type"] == "start"
    assert changes["fetch"]["node_type"] == "node"
    assert changes["report"]["node_type"] == "node"


def test_compare_route_accepts_allow_missing_baseline(tmp_path):
    """HTTP compare route exposes allow_missing_baseline (Studio empty mode):
    a never-published workspace previews the draft instead of a revision error."""
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    workspace = app.state.job_db.create_workspace(
        "Empty",
        default_workflow_key="education_video_problems_generation",
    )
    workspace_id = workspace["id"]
    definition = load_builtin_definition("education_video_problems_generation")
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        without_flag = client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/compare",
            json={"definition_yaml": raw},
        )
        with_flag = client.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/compare",
            json={"definition_yaml": raw, "allow_missing_baseline": True},
        )

    assert without_flag.status_code == 200
    assert without_flag.json()["valid"] is False
    assert without_flag.json()["errors"][0]["category"] == "revision"
    assert with_flag.status_code == 200
    body = with_flag.json()
    assert body["valid"] is True
    assert body["base_revision"] is None
    assert body["draft_workflow"]["version"] == 0
    assert len(body["summary"]["node_changes"]) == len(definition.nodes)
    assert any(flag["code"] == "no_baseline" for flag in body["summary"]["risk_flags"])


def test_compare_rejects_draft_key_mismatch(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_builtin_definition("education_video_problems_generation")
    raw = definition_to_yaml(definition).replace(
        "key: education_video_problems_generation",
        "key: changed_workflow_key",
    )

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is False
    assert result["summary"] is None
    assert result["base_revision"] is None
    assert result["draft_workflow"] is None
    assert len(result["errors"]) == 1
    assert result["errors"][0]["category"] == "schema"
    assert "changed_workflow_key" in result["errors"][0]["message"]
    assert "education_video_problems_generation" in result["errors"][0]["message"]


def test_compare_aggregate_risk_breaking_wins(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_builtin_definition("education_video_problems_generation")
    # Add a node (info) and remove an output (breaking) before serializing.
    definition.nodes["extra_node"] = definition.nodes["intake_knowledge_points"].__class__(
        key="extra_node",
        label="额外节点",
        capability="extra_capability",
        after=[],
        inputs=[],
        outputs=[],
        terminal=None,
    )
    node = definition.nodes["intake_knowledge_points"]
    definition.nodes["intake_knowledge_points"] = node.__class__(
        key=node.key,
        label=node.label,
        capability=node.capability,
        after=node.after,
        inputs=node.inputs,
        outputs=[],
        terminal=node.terminal,
    )
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    assert result["summary"]["risk_level"] == "breaking"
    assert any(c["risk"] == "info" for c in result["summary"]["node_changes"])
    assert any(c["risk"] == "breaking" for c in result["summary"]["node_changes"])


def test_compare_workflow_label_changed_returns_info_metadata_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_builtin_definition("education_video_problems_generation")
    object.__setattr__(definition, "label", f"{definition.label} v2")
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    change = result["summary"]["metadata_changes"][0]
    assert change["field"] == "label"
    assert change["risk"] == "info"
    assert result["summary"]["risk_level"] == "info"


def test_compare_schema_version_changed_returns_breaking_metadata_change(app_with_workspace):
    app, workspace_id = app_with_workspace
    definition = load_builtin_definition("education_video_problems_generation")
    raw = definition_to_yaml(definition).replace("schema_version: 2", "schema_version: 3")

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    change = result["summary"]["metadata_changes"][0]
    assert change["field"] == "schema_version"
    assert change["risk"] == "breaking"
    assert result["summary"]["risk_level"] == "breaking"


def test_compare_start_contract_change_returns_breaking_change(app_with_workspace):
    """Start 节点的 accepted_item_types 是入口契约：变更进 diff 且触发新 revision。"""
    app, workspace_id = app_with_workspace
    definition = load_builtin_definition("education_video_problems_generation")
    start = definition.nodes["_start"]
    definition.nodes["_start"] = replace(start, accepted_item_types=("material", "ref"))
    raw = definition_to_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw)

    assert result["valid"] is True
    assert result["creates_revision"] is True
    change = next(c for c in result["summary"]["node_changes"] if c["node_key"] == "_start")
    assert change["type"] == "modified"
    assert change["fields"] == ["accepted_item_types"]
    assert change["risk"] == "breaking"
    assert any(
        flag["code"] == "accepted_item_types_changed" for flag in result["summary"]["risk_flags"]
    )


def test_compare_corrupt_active_revision_degrades_to_invalid_schema(app_with_workspace):
    """#204 窄化：基线 revision 的 definition_json 损坏（截断的 JSON）→
    invalid compare（schema 类错误），不是 500。"""
    app, workspace_id = app_with_workspace
    with app.state.job_db.write() as conn:
        conn.execute(
            "update workflow_revisions set definition_json='{truncated'"
            " where workflow_key='education_video_problems_generation'"
        )

    definition = load_builtin_definition("education_video_problems_generation")
    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, definition_to_yaml(definition))

    assert result["valid"] is False
    assert result["summary"] is None
    error = result["errors"][0]
    assert error["category"] == "schema"
    assert "Failed to parse active revision" in error["message"]


def test_compare_shape_invalid_active_revision_degrades_to_invalid_schema(app_with_workspace):
    """#204 窄化：基线 revision 结构损坏（nodes 为 list）→ #243 加固的
    WorkflowDefinitionError 同样落入 schema 降级分支。"""
    app, workspace_id = app_with_workspace
    corrupt = '{"key": "education_video_problems_generation", "label": "L", "nodes": []}'
    with app.state.job_db.write() as conn:
        conn.execute(
            "update workflow_revisions set definition_json=%s"
            " where workflow_key='education_video_problems_generation'",
            (corrupt,),
        )

    definition = load_builtin_definition("education_video_problems_generation")
    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, definition_to_yaml(definition))

    assert result["valid"] is False
    assert result["summary"] is None
    assert result["errors"][0]["category"] == "schema"
    assert "Failed to parse active revision" in result["errors"][0]["message"]
