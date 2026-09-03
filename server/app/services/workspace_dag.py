import json
from dataclasses import asdict

from server.app.jobs import JobQueries
from server.app.services.job_errors import NotFoundError
from server.app.services.job_node_ordering import effective_after
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
                # #417：after 与 job 详情视图同源（effective_after：顶层
                # edges 优先派生）——schema v2 的 YAML 只写顶层 edges 时
                # 节点 after 为空，直读原始字段会把边丢掉。
                # 与 job 详情的差异（#424 review P2-1）：本视图保留
                # _start 节点且响应没有独立 edges 字段，客户端只能从
                # nodes[].after 重建拓扑，_start -> root 入口边必须保留
                # （exclude_start=False），否则入口节点永远孤立。
                "after": effective_after(definition, node.key, exclude_start=False),
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
