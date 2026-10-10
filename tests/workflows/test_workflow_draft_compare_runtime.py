"""Issue #1114: 草稿对比 creates_revision 与发布路径同一判定（runtime 属结构）。

compare 曾按自己的字段名清单判定、把一切 ``execution`` 变更当作可原地应用，
于是仅改 ``execution.runtime`` 的草稿被判 ``creates_revision=false``，界面
「应用到运行」承诺「不产生新版本」，而发布路径（``structural_payload``）把
effective runtime 计入结构、实际发布了新 revision。修复后两侧共用
``revision_structurally_changed``；这里按决策表参数化钉住：

| 草稿改动                              | creates_revision | 发布路径       |
|---------------------------------------|------------------|----------------|
| 节点 execution.runtime                | true             | 新 revision    |
| 顶层 execution.runtime（节点继承）    | true             | 新 revision    |
| 节点 provider/model/thinking/prompt/prompt_mode | false  | 原地更新       |
| 顶层 provider/model/thinking          | false            | 原地更新       |
"""

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
# 演示 DAG：顶层 execution.runtime=velites，四个 agent 节点全部继承。
_AGENT_NODE = "write_script"


# ---------------------------------------------------------------------------
# Unit level: the shared judgment over definitions (no DB).
# ---------------------------------------------------------------------------


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


@pytest.mark.no_db
def test_revision_structurally_changed_top_level_defaults():
    """顶层 execution 块本身不计结构；只有它改变了某节点的 effective runtime 才计。"""
    base = _definition(_agent_node())
    assert (
        revision_structurally_changed(
            base, replace(base, execution=WorkflowNodeExecution(provider="p", model="m"))
        )
        is False
    )
    assert revision_structurally_changed(base, base) is False


# ---------------------------------------------------------------------------
# HTTP level: compare route + the publish path agree on every row.
# ---------------------------------------------------------------------------


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


def _top_level_runtime(raw: dict) -> None:
    raw["execution"] = {"runtime": "pi"}


def _node_execution(**values: str):
    def mutate(raw: dict) -> None:
        raw["nodes"][_AGENT_NODE]["execution"] = dict(values)

    return mutate


def _top_level_execution(**values: str):
    def mutate(raw: dict) -> None:
        raw["execution"] = {"runtime": "velites", **values}

    return mutate


@pytest.mark.parametrize(
    ("mutate", "creates_revision", "runtime_nodes"),
    [
        pytest.param(_node_runtime, True, {_AGENT_NODE}, id="node-runtime"),
        pytest.param(
            _top_level_runtime,
            True,
            {"write_script", "review_script", "generate_questions", "review_questions"},
            id="top-level-runtime",
        ),
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
        pytest.param(_top_level_execution(model="gpt-x"), False, set(), id="top-level-model"),
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
    # 改了 runtime 的节点单独标出 runtime 字段（前端据此说明「需发布新版本」）。
    changed = {
        change["node_key"]
        for change in result["summary"]["node_changes"]
        if "runtime" in change["fields"]
    }
    assert changed == runtime_nodes
    if not creates_revision:
        # 原地可应用的改动只报告为 execution 变更（面板据此提供「应用到运行」）。
        node_changes = result["summary"]["node_changes"]
        assert node_changes
        assert all(change["fields"] == ["execution"] for change in node_changes)

    # 发布路径实测：creates_revision 与是否真的出新版本一致。
    service = WorkflowRevisionService(app.state.job_db)
    before = service.get_active(workspace_id, definition.key)
    after = service.save_workspace_revision(
        workspace_id, workflow_definition_from_yaml_string(draft_yaml)
    )
    assert (int(after["version"]) != int(before["version"])) is creates_revision
