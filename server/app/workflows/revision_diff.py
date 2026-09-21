"""新旧 workflow revision 的产物名差集（#759 预算拆分自 upgrade staging）。

失效判定**按名**而非按节点对：旧产出若在新定义仍被任一节点消费
（含跨节点转移、新 RMW 名）就是种子而非垃圾。纯函数，不触库不触盘。
"""

from __future__ import annotations

from server.app.workflows.definition import WorkflowDefinition


def dropped_artifact_names(
    new_definition: WorkflowDefinition, old_definition: WorkflowDefinition
) -> set[str]:
    """旧定义全部 output − 新定义全部 output − 新定义全部 input。"""
    new_outputs = {name for node in new_definition.nodes.values() for name in node.outputs}
    new_inputs = {name for node in new_definition.nodes.values() for name in node.inputs}
    old_outputs = {name for node in old_definition.nodes.values() for name in node.outputs}
    return old_outputs - new_outputs - new_inputs


def removed_node_keys(
    new_definition: WorkflowDefinition, old_definition: WorkflowDefinition
) -> list[str]:
    """旧定义有、新定义没有的可执行节点（排序确定序）。"""
    return sorted(set(old_definition.executable_nodes) - set(new_definition.executable_nodes))


def removed_rmw_names(old_definition: WorkflowDefinition, removed_keys: list[str]) -> list[str]:
    """被删节点的 RMW 名（同名 input+output）：节点已消失，#114 的死等
    理由不成立，必须强制暂存让三者全失效。"""
    return sorted(
        {
            name
            for key in removed_keys
            for name in set(old_definition.nodes[key].outputs)
            & set(old_definition.nodes[key].inputs)
        }
    )
