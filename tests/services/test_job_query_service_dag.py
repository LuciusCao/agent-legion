"""schema-v2 DAG 边派生回归钉（issue #417）。

从 test_job_query_service.py 按被测主题拆出（该文件已超 800 行拆分纪律）：
本模块只收「顶层 edges 派生 after」主题——schema v2 的 YAML 只写顶层 edges
（节点 after 为空或仅残留旧 echo），job 详情与 workspace DAG 视图的边都必须
由顶层 edges 派生，不得因直读原始 after 字段而退化为无边图，也不得从
stale after echo 复活已删除的边（#424 codex 四轮）。
"""

import pytest

from server.app.services.job_queries import JobQueryService
from server.app.services.workflow_revisions import WorkflowRevisionService
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)


@pytest.fixture
def query_service(job_db, settings):
    return JobQueryService(
        job_db,
        settings,
        WorkspaceExecutionConfigurationService(job_db),
    )


_SCHEMA_V2_EDGES_ONLY_YAML = """
key: schema_v2_edges_only
label: Schema v2 edges-only DAG
schema_version: 2
nodes:
  _start:
    label: 入口
    type: start
  intake:
    label: 读取
    type: code
    capability: intake
  expand_analysis:
    label: 撰写扩展详解
    type: code
    capability: expand
  publish:
    label: 汇总
    type: code
    capability: publish
    terminal:
      outcome: published
edges:
  - from: _start
    to: intake
  - from: intake
    to: expand_analysis
  - from: expand_analysis
    to: publish
"""


def _publish_schema_v2_edges_only_revision(job_db, workspace_id: str):
    import yaml as _yaml

    from server.app.workflows.definition import workflow_definition_from_mapping

    definition = workflow_definition_from_mapping(_yaml.safe_load(_SCHEMA_V2_EDGES_ONLY_YAML))
    # schema v2 + loader 不回填 after：该 fixture 的节点 after 必须全空，
    # 否则测试退化成「after 与 edges 双写」而非 issue #417 的形态。
    assert all(not node.after for node in definition.nodes.values())
    return WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)


_SCHEMA_V2_STALE_AFTER_ECHO_YAML = """
key: schema_v2_stale_after_echo
label: Schema v2 stale after echo
schema_version: 2
nodes:
  _start:
    label: 入口
    type: start
  intake:
    label: 读取
    type: code
    capability: intake
  publish:
    label: 汇总
    type: code
    capability: publish
    # stale echo：intake -> publish 的依赖边已从下方 edges 删除，但节点
    # after 仍残留旧值（schema v2 的 after 由序列化无条件回填，不是拓扑源）。
    after:
      - intake
    terminal:
      outcome: published
edges:
  - from: _start
    to: intake
"""


def _publish_schema_v2_stale_after_echo_revision(job_db, workspace_id: str):
    import yaml as _yaml

    from server.app.workflows.definition import workflow_definition_from_mapping

    definition = workflow_definition_from_mapping(_yaml.safe_load(_SCHEMA_V2_STALE_AFTER_ECHO_YAML))
    # 钉死前提：loader 不清理 v2 的 after echo，且 publish 确实没有任何
    # 顶层入边——否则测试退化成普通派生用例而非 #424 codex 四轮的形态。
    assert definition.nodes["publish"].after == ["intake"]
    assert not any(edge.target == "publish" for edge in definition.edges)
    return WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)


def test_job_detail_edges_derive_from_top_level_edges_schema_v2(query_service, job_db):
    workspace = job_db.create_workspace("schema_v2_ws", default_workflow_key="schema_v2_edges_only")
    _publish_schema_v2_edges_only_revision(job_db, workspace["id"])
    batch = job_db.create_run(
        "schema_v2_edges_only",
        "batch_by_ids",
        {"question_ids": ["Q1"]},
        workspace_id=workspace["id"],
    )
    job = job_db.create_job(
        workflow_key="schema_v2_edges_only",
        source_type="question",
        source_id="Q1",
        run_id=batch["id"],
        title="Question 1",
        node_keys=["intake", "expand_analysis", "publish"],
        workspace_id=workspace["id"],
    )

    detail = query_service.detail(job["id"])

    nodes = {node["node_key"]: node for node in detail["nodes"]}
    # 全部边经 effective_after 派生：_start 边隐藏（入口不进 job 视图），
    # 其余链路完整——前端 toDagEdges 据此重建 DAG，缺一条就退化为无边图。
    assert nodes["intake"]["after"] == []
    assert nodes["expand_analysis"]["after"] == ["intake"]
    assert nodes["publish"]["after"] == ["expand_analysis"]


def test_workspace_dag_after_derives_from_top_level_edges_schema_v2(query_service, job_db):
    workspace = job_db.create_workspace("schema_v2_ws", default_workflow_key="schema_v2_edges_only")
    _publish_schema_v2_edges_only_revision(job_db, workspace["id"])

    payload = query_service.workspace_dag(workspace["id"])

    nodes = {node["key"]: node for node in payload["nodes"]}
    # 与 job 详情同样按顶层 edges 派生，业务链路完整；直读原始
    # node.after 会得到三张无边图（issue #417 的 workspace 侧症状）。
    assert nodes["expand_analysis"]["after"] == ["intake"]
    assert nodes["publish"]["after"] == ["expand_analysis"]
    # 差异点（#424 review P2-1）：workspace 视图保留 _start 节点且响应
    # 无独立 edges 字段，客户端只能从 nodes[].after 重建拓扑——
    # _start -> root 入口边必须保留，否则入口节点永远孤立。
    assert nodes["intake"]["after"] == ["_start"]
    # start 节点本身保留在 workspace 视图（definition-level 概念），
    # 但它没有前驱。
    assert nodes["_start"]["after"] == []


def test_workspace_dag_empty_predecessors_when_after_echo_is_stale_schema_v2(query_service, job_db):
    """#424 codex 四轮 P2：无入边节点不从 stale after echo 恢复已删边。

    schema v2 下 definition.edges 是唯一拓扑源（执行器 workflow_branching
    只按它派生就绪）；节点无顶层入边时前驱必须是空数组，回退原始
    node.after 会返回执行器不会采用的边。
    """
    workspace = job_db.create_workspace(
        "schema_v2_stale_ws", default_workflow_key="schema_v2_stale_after_echo"
    )
    _publish_schema_v2_stale_after_echo_revision(job_db, workspace["id"])

    payload = query_service.workspace_dag(workspace["id"])

    nodes = {node["key"]: node for node in payload["nodes"]}
    # publish 没有任何顶层入边：after 为 []，即便原始 after 残留
    # [intake]（已被删除的依赖边，不得复活）。
    assert nodes["publish"]["after"] == []
    # 同一 fixture 内 start 入口边保留（#424 review P2-1 行为不回归）。
    assert nodes["intake"]["after"] == ["_start"]
    assert nodes["_start"]["after"] == []
