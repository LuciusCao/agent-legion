"""悬挂清单行升级项的公告构造（#887，从 ``hydration_dangling`` 拆出，文件预算）。

公告回答 UI 的两个问题：哪些等待节点被这个输入挡住、重跑哪个生产节点能
重新生成它。建议重跑节点与升级 WARNING 的 suggested action 同源。
"""

from __future__ import annotations

from server.app.services.hydration_defer_board import HydrationDeferNotice
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import RUNNABLE_STATUSES, effective_node_statuses
from server.app.workflows.workflow_consumption import artifact_producers


def waiting_nodes(definition: WorkflowDefinition, statuses: dict[str, str], name: str) -> list[str]:
    """被该名字挡住的可运行节点：input 消费者与以它为条件的边 target。

    都不命中时（理论上探针面只来自这两处）退回全部可运行节点——defer 挡的
    是整个 job 的评估，它们确实都在等。
    """
    effective = effective_node_statuses(definition, statuses)
    runnable = {
        key for key in definition.nodes if effective.get(key, "pending") in RUNNABLE_STATUSES
    }
    blocked = {key for key in runnable if name in definition.nodes[key].inputs}
    blocked |= {
        e.target
        for e in definition.edges
        if e.condition is not None and e.condition.artifact == name and e.target in runnable
    }
    return sorted(blocked or runnable)


def rerun_nodes(definition: WorkflowDefinition, name: str, row_node_key: str) -> list[str]:
    """声明该名字为输出的生产节点；定义里已无生产者时退回清单行的写者。"""
    return sorted(artifact_producers(definition).get(name, set())) or [row_node_key]


def defer_notice(
    definition: WorkflowDefinition,
    statuses: dict[str, str],
    name: str,
    outcome: str,
    row_node_key: str,
) -> HydrationDeferNotice:
    return HydrationDeferNotice(
        input_name=name,
        outcome=outcome,
        rerun_nodes=tuple(rerun_nodes(definition, name, row_node_key)),
        waiting_nodes=tuple(waiting_nodes(definition, statuses, name)),
    )
