"""悬挂清单行升级项的公告构造（#887，从 ``hydration_dangling`` 拆出，文件预算）。

公告回答 UI 的两个问题：哪些等待节点被挡住（job 级 gate，即全部可运行
节点）、重跑哪个生产节点能重新生成它。建议重跑节点与升级 WARNING 的 suggested action 同源。
"""

from __future__ import annotations

from server.app.services.hydration_defer_board import HydrationDeferNotice
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import RUNNABLE_STATUSES, effective_node_statuses
from server.app.workflows.workflow_consumption import artifact_producers


def waiting_nodes(definition: WorkflowDefinition, statuses: dict[str, str]) -> list[str]:
    """被挡住的等待节点：当前全部可运行节点。

    hydration defer 是 job 级 gate（``eval_hydration`` 任一输入未恢复即跳过
    整个 job 的评估），与悬挂输入无关的独立分支本轮同样进不了候选队列——
    只标直接消费者会把它们误显示为普通排队（codex #1018 P2）。
    """
    effective = effective_node_statuses(definition, statuses)
    return sorted(
        key for key in definition.nodes if effective.get(key, "pending") in RUNNABLE_STATUSES
    )


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
        waiting_nodes=tuple(waiting_nodes(definition, statuses)),
    )
