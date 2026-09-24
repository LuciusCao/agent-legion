"""分支条件产物的生产者屏障（#759 ③ 对抗复审 P1）。

条件 artifact 有非终态生产者时，分支判定不可信——缺失（暂存删除/尚未
产出）会被 ``condition_matches`` 当 false（gated 分支被永久标
not_applicable），保留的旧字节（RMW）会被当真值走错分支。判定推迟到
生产者终态之后：不选边、不标 not_applicable、不就绪。

与调度就绪的隐式生产者屏障（scheduler.find_ready_nodes 的
``_has_unfinished_implicit_producer``）共用同一张生产者索引
（``artifact_producers``）与同一组终态集合：not_applicable 生产者不设障
（其产物本轮不刷新，读既有文件与文件存在语义一致）；failed 生产者设障
无害（failed-upstream 防线会先拦住整条支路，job 已失败）。

自门控排除集（#759 ③ 二轮对抗复审 P1）：被门控分支内部（含 target 自
身，含合并闭包意义上的隐式下游）的生产者本来就跑不到（等分支被选中），
对它们设障是循环等待（永久静默挂起）——这类「条件由分支内部产物决定」
的定义按文件语义评估（缺失即 false），与屏障引入前一致。已知交互（③ 三
轮对抗复审登记，接受）：自门控定义下 rerun target 的上游会重置分支内生
产者并暂存删除条件文件，target 随之被标 not_applicable——该 rerun 意图
被静默吞掉，属文件语义的固有取舍。调用方以 ``branch_gated_keys`` 计算排
除集（``any_condition_producer_in_flight`` 已内置逐边计算）。

自 ``workflow_branching`` 拆出的体积预算姊妹模块。
"""

from __future__ import annotations

from collections.abc import Iterable

from server.app.workflows.definition import WorkflowDefinition, WorkflowEdge
from server.app.workflows.workflow_consumption import dependency_downstream

TERMINAL_SUCCESS_STATUSES = {"completed", "not_applicable"}


def condition_producer_in_flight(
    edge: WorkflowEdge,
    producers: dict[str, set[str]],
    node_statuses: dict[str, str],
    *,
    excluded: frozenset[str] = frozenset(),
) -> bool:
    """条件 artifact 有排除集之外的非终态生产者。"""
    if edge.condition is None:
        return False
    for producer in producers.get(edge.condition.artifact, ()):
        if producer in excluded:
            continue
        if node_statuses.get(producer, "pending") not in TERMINAL_SUCCESS_STATUSES:
            return True
    return False


def branch_gated_keys(definition: WorkflowDefinition, target: str) -> frozenset[str]:
    """target 及其**合并**下游闭包（显式边 ∪ 隐式消费边，
    ``dependency_downstream``）——条件产物生产者的自门控排除集。

    必须用合并闭包而非显式-only（#759 ③ 三轮对抗复审 P1）：loader 不要求
    inputs 的生产者与消费者相邻，「条件产物由 target 的隐式下游生产」
    （probe 消费 target 的 output、产出 gate→target 的条件）的合法定义在
    显式闭包里看不到 probe——probe 永远跑不到（它等 target 的 output），
    对它设障是循环等待（永久静默挂起）。合并闭包正是本仓库的单一事实源
    （`workflow_consumption`），屏障与重置闭包因此同图。"""
    return frozenset(dependency_downstream(definition, target)) | {target}


def any_condition_producer_in_flight(
    edges: Iterable[WorkflowEdge],
    producers: dict[str, set[str]],
    node_statuses: dict[str, str],
    definition: WorkflowDefinition,
) -> bool:
    """``condition_producer_in_flight`` 的集合版：逐边按其 target 的自门控
    闭包排除分支内部生产者（调度就绪闸逐入边判定）。"""
    return any(
        condition_producer_in_flight(
            edge,
            producers,
            node_statuses,
            excluded=branch_gated_keys(definition, edge.target),
        )
        for edge in edges
    )
