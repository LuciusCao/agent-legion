import json
from dataclasses import asdict

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.workflows.definition import workflow_definition_from_dict


def build_workspace_dag(
    job_db: JobQueries,
    workspace_id: str,
) -> dict:
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        raise NotFoundError("Workspace not found")
    workflow_key = str(workspace.get("default_workflow_key") or "")
    if not workflow_key:
        raise NotFoundError("Workspace workflow is not set")
    active = job_db.get_active_workflow_revision(workspace_id, workflow_key)
    if active is None:
        raise NotFoundError("Workspace has no active workflow revision")
    definition = workflow_definition_from_dict(json.loads(str(active["definition_json"])))
    counts = job_db.count_workspace_job_nodes_by_status(workspace_id, workflow_key)
    statuses = ["pending", "running", "completed", "failed", "stale"]
    # #424 codex 四轮 P2：前驱直接按顶层 definition.edges 派生（含 _start
    # 入口边，等价此前 effective_after 的 exclude_start=False——本视图保留
    # _start 节点且响应没有独立 edges 字段，客户端只能从 nodes[].after 重建
    # 拓扑，入口边不能丢）。不走 effective_after 是因为它的 fallback（无入边
    # 时回退原始 node.after）会复活 schema v2 下已删除的边：v2 的 after 只是
    # 序列化时无条件回填的旧 echo（revision_format.definition_to_yaml），
    # 在 Studio 只删 edges 里的依赖边时节点无入边但 after 仍非空，而执行器
    # 只按 definition.edges 派生就绪（workflow_branching），回退返回的就是
    # 执行器不会采用的边。loader 对两种 schema 都保证 definition.edges 是
    # 完整拓扑（v1 由 _load_edges 从 after 回填，v2 只认顶层 edges），因此
    # 无入边节点的前驱就是空数组。job 视图仍走 effective_after：其 fallback
    # 并非 v1 遗留数据的唯一边来源（loader 已回填），仅作防御兼容保留。
    predecessors: dict[str, list[str]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        predecessors[edge.target].append(edge.source)
    return {
        "workflow": {
            "key": definition.key,
            "label": definition.label,
        },
        "nodes": [
            {
                "key": node.key,
                "label": node.label,
                "capability": node.capability,
                # #417：schema v2 的 YAML 只写顶层 edges 时节点 after 为空，
                # 直读原始字段会把边丢掉——前驱一律按 edges 派生（上方
                # predecessors），不回退原始 after（#424 codex 四轮 P2）。
                "after": predecessors[node.key],
                "inputs": node.inputs,
                "outputs": node.outputs,
                "execution": asdict(node.execution),
                "status_counts": {
                    status: counts.get(node.key, {}).get(status, 0) for status in statuses
                },
            }
            for node in definition.nodes.values()
        ],
    }
