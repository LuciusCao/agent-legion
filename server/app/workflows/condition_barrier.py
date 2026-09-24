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
身）的生产者本来就跑不到（等分支被选中），对它们设障是循环等待（永久
静默挂起）——这类「条件由分支内部产物决定」的定义按文件语义评估（缺
失即 false），与屏障引入前一致。调用方以 ``branch_gated_keys`` 计算排除
集（``any_condition_producer_in_flight`` 已内置逐边计算）。

自 ``workflow_branching`` 拆出的体积预算姊妹模块。
"""

from __future__ import annotations

from collections.abc import Iterable

from server.app.workflows.definition import WorkflowDefinition, WorkflowEdge

TERMINAL_SUCCESS_STATUSES = {"completed", "not_applicable"}


def _explicit_reachable(definition: WorkflowDefinition, start: str) -> set[str]:
    """显式边的传递下游（含 start 自身）。"""
    children: dict[str, list[str]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        children[edge.source].append(edge.target)
    seen: set[str] = set()
    stack = [start]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        stack.extend(children.get(key, []))
    return seen


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
    """target 及其显式下游闭包——条件产物生产者的自门控排除集（见
    ``condition_producer_in_flight`` 的 ``excluded``）。"""
    return frozenset(_explicit_reachable(definition, target) | {target})


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
