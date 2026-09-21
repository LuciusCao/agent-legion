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
    论证，必须由调用方排除。
    """
    skipped = set(skip_names)
    producers = artifact_producers(definition)
    edges: dict[str, set[str]] = {key: set() for key in definition.nodes}
    for key, node in definition.nodes.items():
        for name in node.inputs:
            if name in skipped:
                continue
            for producer in producers.get(name, ()):
                if producer != key:
                    edges[producer].add(key)
    return {key: sorted(targets) for key, targets in edges.items()}


def artifact_producers(definition: WorkflowDefinition) -> dict[str, set[str]]:
    """产物名 → 声明其为 output 的节点集合（调度完成屏障与隐式边共用）。

    调度器（find_ready_nodes）与重置语义（本模块的隐式边）必须使用同一个
    生产者索引，否则「RMW 产物在重置后被刻意保留」的场景里消费者会和
    生产者并发重跑、读到旧值（#759 codex P1）。
    """
    producers: dict[str, set[str]] = {}
    for key, node in definition.nodes.items():
        for name in node.outputs:
            producers.setdefault(name, set()).add(key)
    return producers


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


def dependency_parents(
    definition: WorkflowDefinition, *, skip_consumption_names: Iterable[str] = ()
) -> dict[str, list[str]]:
    """合并上游邻接表：显式边 ∪ 隐式生产边（key → 排序后的直接上游）。

    下游合并了而上游没有，会让「隐式生产者 failed」逃出所有 failed-
    upstream 防线：守卫放行 → 重置提交 → 调度的隐式生产者完成屏障
    永久阻塞目标（#759 自审 P1）。上游判定必须与下游同一张合并图。
    """
    parents: dict[str, set[str]] = {key: set() for key in definition.nodes}
    for source, targets in dependency_children(
        definition, skip_consumption_names=skip_consumption_names
    ).items():
        for target in targets:
            parents[target].add(source)
    return {key: sorted(sources) for key, sources in parents.items()}


def dependency_ancestors(definition: WorkflowDefinition, node_key: str) -> list[str]:
    """节点的合并传递上游（显式边 ∪ 隐式生产边），排序确定序。"""
    return sorted(walk_downstream(dependency_parents(definition), [node_key]))
