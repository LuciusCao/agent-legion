"""Issue #431: compare 对 node_type/after 序/shard/reduce/edges 序的比对。

姊妹文件（主文件 test_workflow_draft_compare.py 已接近 test_max_lines 上限，
#422 拆分纪律延续）：这里聚焦 _structural_payload 判结构性变更、而 compare
曾经盲区的五类字段。publish 出新版本 + compare 报零变更 = canPublish 的
hasCompareChanges 闸门禁用发布按钮（#418 原始症状同型）。
"""

from dataclasses import replace

import yaml
from fastapi.testclient import TestClient

from server.app.main import create_app
from server.app.services.workflow_draft_compare import _node_change_fields
from server.app.services.workflow_drafts import workflow_definition_from_yaml_string
from server.app.services.workflow_revision_change import structural_revision_changed
from server.app.services.workflow_revision_format import definition_to_yaml
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.workflows.schema import (
    WorkflowNode,
    WorkflowReduceSpec,
    WorkflowShardSpec,
)
from tests.helpers import load_builtin_definition
from tests.helpers.auth import authenticate_client


def _app_with_baseline(tmp_path):
    """App + workspace whose active revision is the demo DAG (HTTP 路径)."""
    app = create_app(data_dir=tmp_path, start_worker=False)
    response = authenticate_client(TestClient(app)).post(
        "/api/workspaces",
        json={"id": "education_video_problems_generation", "name": "Studio"},
    )
    workspace_id = response.json()["workspace"]["id"]
    definition = load_builtin_definition("education_video_problems_generation")
    WorkflowRevisionService(app.state.job_db).publish_workspace_revision(workspace_id, definition)
    return app, workspace_id, definition


def _sharded_draft_yaml(definition) -> str:
    """Demo DAG + review_questions 分片其 exercises 输入（合法 shard 形态）。

    reduce 无法走 HTTP 路径做「基线→草稿」对比：asdict 快照里 WorkflowReduceSpec
    序列化为 ``from_node``，而 loader 的 yaml 拼写是 ``from``，含 reduce 的基线
    在 compare 的基线解析臂直接报 schema 错误（独立的快照往返缺陷，不在 #431
    范围）；shard 不经拼写映射，可正常往返，故 reduce 的 only 变更用单元级
    _node_change_fields + structural_revision_changed 覆盖。
    """
    raw = yaml.safe_load(definition_to_yaml(definition))
    raw["nodes"]["review_questions"]["shard"] = {"over": "inputs.exercises.json"}
    return yaml.safe_dump(raw, allow_unicode=True)


def _compare(client: TestClient, workspace_id: str, definition_yaml: str) -> dict:
    response = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/compare",
        json={"definition_yaml": definition_yaml},
    )
    assert response.status_code == 200
    return response.json()


def _modified_change(result: dict, node_key: str) -> dict:
    return next(
        c
        for c in result["summary"]["node_changes"]
        if c["node_key"] == node_key and c["type"] == "modified"
    )


def _bare_node(key: str = "demo") -> WorkflowNode:
    return WorkflowNode(key=key, label=key, capability="demo")


# ---------------------------------------------------------------------------
# Unit level: _node_change_fields covers the five remaining structural fields.
# ---------------------------------------------------------------------------


def test_node_change_fields_node_type_change_is_change():
    base = _bare_node()
    draft = replace(base, node_type="agent")
    assert _node_change_fields(base, draft) == ["node_type"]


def test_node_change_fields_after_order_is_change():
    """after 是有序 list：同集不同序即变更（对齐 _structural_payload 的 ==）。"""
    base = _bare_node()
    draft = replace(base, after=["a", "b"])
    assert _node_change_fields(replace(base, after=["b", "a"]), draft) == ["after"]
    # Same order: no change.
    assert _node_change_fields(replace(base, after=["a", "b"]), draft) == []


def test_node_change_fields_shard_change_is_change():
    base = _bare_node()
    sharded = replace(base, shard=WorkflowShardSpec(count=4))
    assert _node_change_fields(base, sharded) == ["shard"]
    # Same value objects: no change (dataclass equality, aligned with asdict ==).
    assert _node_change_fields(sharded, replace(sharded, shard=WorkflowShardSpec(count=4))) == []


def test_node_change_fields_reduce_change_is_change():
    base = _bare_node()
    reducing = replace(base, reduce=WorkflowReduceSpec(from_node="sharded"))
    assert _node_change_fields(base, reducing) == ["reduce"]
    assert _node_change_fields(
        reducing, replace(reducing, reduce=WorkflowReduceSpec(from_node="x"))
    ) == ["reduce"]
    # Same value objects: no change.
    assert (
        _node_change_fields(
            reducing, replace(reducing, reduce=WorkflowReduceSpec(from_node="sharded"))
        )
        == []
    )


def test_structural_revision_changed_for_remaining_fields():
    """传导链：五类新字段必须让 creates_revision 为真（对齐 publish 行为）。"""
    for field in ("node_type", "after", "shard", "reduce"):
        node_change = {"type": "modified", "fields": [field]}
        assert structural_revision_changed([node_change], [], [], []) is True
    # edges reorder lands in the edges dimension: any edge change is structural.
    assert structural_revision_changed([], [{"type": "reordered"}], [], []) is True
    # execution-only stays the runtime exception (regression guard, #431 spec).
    assert (
        structural_revision_changed([{"type": "modified", "fields": ["execution"]}], [], [], [])
        is False
    )


# ---------------------------------------------------------------------------
# HTTP level: the exact issue #431 scenarios through the studio compare route.
# ---------------------------------------------------------------------------


def test_compare_node_type_only_change_creates_revision(tmp_path):
    """Issue #431 精确场景：code→agent 切换、capability 不变、无 skill 绑定
    ——node_type 是唯一差异，compare 必须产生 node_changes 并判新版本。"""
    app, workspace_id, definition = _app_with_baseline(tmp_path)
    node = definition.nodes["publish_content"]
    assert node.node_type == "code"  # the switchable baseline
    definition.nodes["publish_content"] = replace(node, node_type="agent")

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, definition_to_yaml(definition))

    assert result["valid"] is True
    assert result["creates_revision"] is True
    change = _modified_change(result, "publish_content")
    assert change["fields"] == ["node_type"]
    assert change["risk"] == "breaking"


def test_compare_after_reorder_creates_revision(tmp_path):
    """同集不同序的 after（publish_content 的两个依赖互换）是结构性变更。"""
    app, workspace_id, definition = _app_with_baseline(tmp_path)
    node = definition.nodes["publish_content"]
    assert node.after == ["review_script", "review_questions"]
    definition.nodes["publish_content"] = replace(node, after=["review_questions", "review_script"])

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, definition_to_yaml(definition))

    assert result["valid"] is True
    assert result["creates_revision"] is True
    change = _modified_change(result, "publish_content")
    assert change["fields"] == ["after"]
    assert change["risk"] == "warning"


def test_compare_shard_only_change_creates_revision(tmp_path):
    app, workspace_id, definition = _app_with_baseline(tmp_path)
    raw_yaml = _sharded_draft_yaml(definition)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw_yaml)

    assert result["valid"] is True
    assert result["creates_revision"] is True
    change = _modified_change(result, "review_questions")
    assert change["fields"] == ["shard"]
    assert change["risk"] == "warning"


def test_compare_sharded_baseline_roundtrip_reports_no_change(tmp_path):
    """带 shard 的基线做无变更 round-trip：不得误报（回归护栏）。

    注：definition_to_yaml 的回显不落 shard（YAML 拼写缺失，同 reduce 的
    from/from_node——独立的回显缺陷，不在 #431 范围），所以草稿用同一份
    原始 YAML 而非回显：基线与草稿两侧都过 loader，fields 比对必须稳定
    为空。
    """
    app, workspace_id, definition = _app_with_baseline(tmp_path)
    raw_yaml = _sharded_draft_yaml(definition)
    baseline = workflow_definition_from_yaml_string(raw_yaml)
    WorkflowRevisionService(app.state.job_db).publish_workspace_revision(workspace_id, baseline)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw_yaml)

    assert result["valid"] is True
    assert result["creates_revision"] is False
    assert result["summary"]["node_changes"] == []
    assert result["summary"]["risk_level"] == "none"


def test_compare_edges_reordered_creates_revision(tmp_path):
    """edges 序变化（同集不同序）落在 edges 维度：一条 reordered edge change
    + edges_reordered 风险旗标，creates_revision 为真。

    维度分析：node 级 after 不镜像 edges 序——schema_version 2 的 edges 来自
    顶层 edges 块而非 after 派生，被移动的边也没有归属节点（其 source/target
    的邻居都不变），所以 definition 级的顺序差异只能报在 edge_changes。
    """
    app, workspace_id, definition = _app_with_baseline(tmp_path)
    raw = yaml.safe_load(definition_to_yaml(definition))
    raw["edges"] = [
        {"from": "intake_knowledge_points", "to": "generate_questions"},
        {"from": "_start", "to": "intake_knowledge_points"},
        {"from": "intake_knowledge_points", "to": "write_script"},
        {"from": "write_script", "to": "review_script"},
        {"from": "generate_questions", "to": "review_questions"},
        {"from": "review_questions", "to": "publish_content"},
        {"from": "review_script", "to": "publish_content"},
    ]
    raw_yaml = yaml.safe_dump(raw, allow_unicode=True)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw_yaml)

    assert result["valid"] is True
    assert result["creates_revision"] is True
    edge_changes = result["summary"]["edge_changes"]
    assert [c["type"] for c in edge_changes] == ["reordered"]
    assert edge_changes[0]["risk"] == "info"
    assert result["summary"]["node_changes"] == []  # 节点本身无变化
    assert any(flag["code"] == "edges_reordered" for flag in result["summary"]["risk_flags"])
    assert result["summary"]["risk_level"] == "info"


def test_compare_no_change_roundtrip_stays_empty(tmp_path):
    """既有护栏：基线 round-trip 无变更仍报零变更（五类新字段不误报）。"""
    app, workspace_id, definition = _app_with_baseline(tmp_path)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, definition_to_yaml(definition))

    assert result["valid"] is True
    assert result["creates_revision"] is False
    assert result["summary"]["node_changes"] == []
    assert result["summary"]["edge_changes"] == []
    assert result["summary"]["risk_flags"] == []
