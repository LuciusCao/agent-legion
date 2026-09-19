"""隐式消费边与合并下游闭包（issue #759 P1）。

loader 对节点 ``inputs`` 只做字符串列表解析，不要求其生产者在显式边
上游——调度器靠输入文件出现解锁。因此重跑/重置语义的「下游」必须是
显式边 ∪ 隐式消费边：全图 ``output 名 → producer 集合`` 索引，节点 N
的每个有生产者的 input 名构成隐式边 producer→N。RMW 纪律：节点输入
与自己的 output 同名不构成自边（不回传自己），但仍作为生产者向该名
的其他消费者传播；无任何生产者的外部 input 不产生边。

隐式边可能成环（loader 的 acyclic 校验只管显式边）：``walk_downstream``
以 seen 防环，环内节点互相视为下游——保守方向（一起重跑），永不漏。
纯函数：不触库、不触文件系统。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from server.app.workflows.definition import WorkflowDefinition


def consumer_edges(
    definition: WorkflowDefinition, *, skip_names: Iterable[str] = ()
) -> dict[str, list[str]]:
    """隐式消费边索引：producer key → 排序后的 consumer key 列表。

    ``skip_names``（#759 4.1）：排除经由这些名字的隐式边。判定「名 X 的
    consumer 是否保证在某 producer 之后执行」时，X 自己的隐式边正是被
    保留的启动对象 / manifest 回填所满足的等待——拿它当保证证据是循环
    论证，必须由调用方排除。复审 P1（跨名互证）起调用方把排除面扩大到
    「所有尚未证明本次清理缺席的名字」：经由 RMW 名（不暂存，旧文件存
    活）或受保护名的隐式边不构成因果序，见
    ``job_workflow_upgrade_removed_outputs.unprotected_input_names``。
    """
    skipped = set(skip_names)
    producers: dict[str, set[str]] = {}
    for key, node in definition.nodes.items():
        for name in node.outputs:
            producers.setdefault(name, set()).add(key)
    edges: dict[str, set[str]] = {key: set() for key in definition.nodes}
    for key, node in definition.nodes.items():
        for name in node.inputs:
            if name in skipped:
                continue
            for producer in producers.get(name, ()):
                if producer != key:
                    edges[producer].add(key)
    return {key: sorted(targets) for key, targets in edges.items()}


def dependency_children(
    definition: WorkflowDefinition, *, skip_consumption_names: Iterable[str] = ()
) -> dict[str, list[str]]:
    """合并邻接表：显式边 ∪ 隐式消费边（key → 排序后的直接下游）。

    ``skip_consumption_names``（#759 4.1）：排除经由这些名字的隐式消费
    边，语义见 ``consumer_edges``——只在判定「名 X 的 consumer 是否保证
    在 producer 之后执行」时使用。
    """
    children: dict[str, set[str]] = {key: set() for key in definition.nodes}
    for edge in definition.edges:
        children[edge.source].add(edge.target)
    for source, targets in consumer_edges(definition, skip_names=skip_consumption_names).items():
        children[source].update(targets)
    return {key: sorted(targets) for key, targets in children.items()}


def walk_downstream(children: Mapping[str, Iterable[str]], starts: Iterable[str]) -> set[str]:
    """邻接表上的传递下游（seen 防环，隐式边环内互染）。"""
    seen: set[str] = set()
    stack = [child for start in starts for child in children.get(start, ())]
    while stack:
        child = stack.pop()
        if child in seen:
            continue
        seen.add(child)
        stack.extend(children.get(child, ()))
    return seen


def dependency_downstream(definition: WorkflowDefinition, node_key: str) -> list[str]:
    """节点的合并传递下游（显式边 ∪ 隐式消费边），排序确定序。"""
    return sorted(walk_downstream(dependency_children(definition), [node_key]))
