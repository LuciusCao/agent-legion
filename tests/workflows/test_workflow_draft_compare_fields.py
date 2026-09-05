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


def _sharded_reduce_draft_yaml(definition) -> str:
    """Demo DAG + review_questions 分片其 exercises 输入、publish_content 聚合。

    #458 修复后（loader 翻译快照的 from_node→from、definition_to_yaml 回显
    shard/reduce），含 reduce 的基线可以走 HTTP 路径做「基线→草稿」对比，
    不再被迫降级为单元级测试。
    """
    raw = yaml.safe_load(definition_to_yaml(definition))
    raw["nodes"]["review_questions"]["shard"] = {"over": "inputs.exercises.json"}
    raw["nodes"]["publish_content"]["reduce"] = {"from": "review_questions"}
    return yaml.safe_dump(raw, allow_unicode=True)


def _sharded_draft_yaml(definition) -> str:
    """Demo DAG + review_questions 分片其 exercises 输入（合法 shard 形态）。"""
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

    #458 修复后 definition_to_yaml 会回显 shard/reduce，所以这里的基线与
    草稿同用一份原始 YAML（基线侧经 asdict 快照、草稿侧经 YAML，两侧都过
    loader），fields 比对必须稳定为空。
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


def test_compare_reduce_only_change_creates_revision(tmp_path):
    """reduce-only 变更走 HTTP 路径：基线含 shard+reduce 配对，草稿只删聚合。

    #458 修复前含 reduce 的基线在 compare 的基线解析臂必报 schema 错误
    （from_node 拼写不被 loader 接受），本测试被迫降级为单元级；修复后
    补上完整的 HTTP 级覆盖。
    """
    app, workspace_id, definition = _app_with_baseline(tmp_path)
    baseline_yaml = _sharded_reduce_draft_yaml(definition)
    baseline = workflow_definition_from_yaml_string(baseline_yaml)
    WorkflowRevisionService(app.state.job_db).publish_workspace_revision(workspace_id, baseline)

    raw = yaml.safe_load(baseline_yaml)
    del raw["nodes"]["review_questions"]["shard"]
    del raw["nodes"]["publish_content"]["reduce"]
    raw_yaml = yaml.safe_dump(raw, allow_unicode=True)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw_yaml)

    assert result["valid"] is True
    assert result["creates_revision"] is True
    change = _modified_change(result, "publish_content")
    assert change["fields"] == ["reduce"]
    assert change["risk"] == "warning"


def test_compare_sharded_baseline_echo_yaml_reports_no_change(tmp_path):
    """#458 端到端：studio 幽灵变更消除。

    studio 草稿初始 YAML 来自 active revision 的 definition_yaml 回显——
    修复前含 shard 基线的工作区一打开，对这份「未改动」的回显 compare 就
    报 shard modified（幽灵变更、reset 清不掉，照此发布会静默删分片）；
    修复后回显携带 shard/reduce，等值 round-trip 必须报零变更。
    """
    app, workspace_id, definition = _app_with_baseline(tmp_path)
    baseline_yaml = _sharded_reduce_draft_yaml(definition)
    baseline = workflow_definition_from_yaml_string(baseline_yaml)
    WorkflowRevisionService(app.state.job_db).publish_workspace_revision(workspace_id, baseline)

    # The studio's initial draft is the echo of the just-published revision.
    echo_yaml = definition_to_yaml(baseline)
    assert "shard:" in echo_yaml and "reduce:" in echo_yaml

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, echo_yaml)

    assert result["valid"] is True
    assert result["creates_revision"] is False
    assert result["summary"]["node_changes"] == []
    assert result["summary"]["edge_changes"] == []
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


def test_compare_edges_set_and_order_changed_reports_no_reorder(tmp_path):
    """去重护栏：集合与顺序同时变化时只报 added/removed，不报 reordered。

    reorder 检测的前提是集合不变（sorted 相等）——集合变了就轮不到顺序
    说话，身份 diff 的 added/removed 已完整表达差异；混报 reordered 会
    让无序的 reorder 语义污染有身份语义的变更列表。
    """
    app, workspace_id, definition = _app_with_baseline(tmp_path)
    raw = yaml.safe_load(definition_to_yaml(definition))
    # Keep the start edge (loader requires one outgoing edge from _start) but
    # swap a mid-list edge for a new identity — the set changes *and* the
    # surviving edges all sit at different list positions.
    raw["edges"] = (
        [
            {"from": "_start", "to": "intake_knowledge_points"},
        ]
        + [
            edge
            for edge in raw["edges"]
            if edge
            not in (
                {"from": "_start", "to": "intake_knowledge_points"},
                {"from": "intake_knowledge_points", "to": "write_script"},
            )
        ]
        + [{"from": "write_script", "to": "generate_questions"}]
    )
    raw_yaml = yaml.safe_dump(raw, allow_unicode=True)

    with authenticate_client(TestClient(app)) as client:
        result = _compare(client, workspace_id, raw_yaml)

    assert result["valid"] is True
    assert result["creates_revision"] is True
    types = [c["type"] for c in result["summary"]["edge_changes"]]
    assert sorted(types) == ["added", "removed"]
    assert "reordered" not in types
    assert not any(flag["code"] == "edges_reordered" for flag in result["summary"]["risk_flags"])


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
