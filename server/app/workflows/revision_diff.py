"""新旧 workflow revision 的产物名差集（#759 预算拆分自 upgrade staging）。

失效判定**按名**而非按节点对：旧产出若在新定义仍被消费（含跨节点转移、
新 RMW 名、分支条件种子）就是种子而非垃圾。消费名一律取统一索引
（``artifact_consumption_index`` 的键集 = 节点 inputs ∪ 分支条件产物）
——任何自行重遍历 definition 的枚举都会漏渠道（codex #775 对抗复审
P1：漏掉 ``edge.condition.artifact`` 会把仍被新定义分支条件消费的种子
连文件带清单行删掉）。纯函数，不触库不触盘。
"""

from __future__ import annotations

from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_consumption import artifact_consumption_index


def dropped_artifact_names(
    new_definition: WorkflowDefinition, old_definition: WorkflowDefinition
) -> set[str]:
    """旧定义全部 output − 新定义全部 output − 新定义全部消费名（统一索引键集）。"""
    new_outputs = {name for node in new_definition.nodes.values() for name in node.outputs}
    old_outputs = {name for node in old_definition.nodes.values() for name in node.outputs}
    return old_outputs - new_outputs - set(artifact_consumption_index(new_definition))


def removed_node_keys(
    new_definition: WorkflowDefinition, old_definition: WorkflowDefinition
) -> list[str]:
    """旧定义有、新定义没有的可执行节点（排序确定序）。"""
    return sorted(set(old_definition.executable_nodes) - set(new_definition.executable_nodes))
