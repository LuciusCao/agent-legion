"""artifact 消费关系单一事实源与合并下游闭包（issue #759）。

loader 对节点 ``inputs`` 只做字符串列表解析，不要求其生产者在显式边
上游——调度器靠输入文件出现解锁；``edge.condition.artifact`` 同理：
分支评估在 source 完成后读 job_dir 里的条件文件决定是否激活 target，
loader 同样不要求该名的生产者与边相邻。因此重跑/重置语义的「下游」
必须覆盖全部消费渠道，本模块是唯一枚举处（``artifact_consumption_index``）：

- ``node.inputs`` 声明：名 X 的每个生产者 → 声明 X 的节点；
- RMW：节点输入与自己的 output 同名不构成自边（不回传自己），但仍作为
  生产者向该名的其他消费者传播；无任何生产者的外部 input 不产生边；
- 分支条件产物：``edge.condition.artifact`` 的每个生产者 → 该边的
  target（分支评估替 target 读这份文件；生产者与 source 不相邻时这是
  唯一的传播通道——少了它，重跑/升级会留下旧条件字节，分支评估静默
  走错分支）。

隐式边可能成环（loader 的 acyclic 校验只管显式边）：``walk_downstream``
以 seen 防环，环内节点互相视为下游——保守方向（一起重跑），永不漏。
纯函数：不触库、不触文件系统。

注意：本模块的隐式边只表达「文件级消费关系」，不构成执行顺序证据——
consumer 是否真的等 producer 重跑取决于该名字本次是否三面删除（缺席
即闸）。upgrade 输入保护计划的「保证先行」判定在
``server/app/services/job_workflow_upgrade_protection.py``，那里只把
「唯一生产者且本次会缺席」的名字的隐式边当排序证据（#759 复审 P1-A）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from server.app.workflows.definition import WorkflowDefinition


def artifact_consumption_index(definition: WorkflowDefinition) -> dict[str, frozenset[str]]:
    """artifact 名 → 消费它的节点 key 集合（全部消费渠道的唯一枚举）。

    - 节点 ``inputs`` 里的每个名字：声明节点是消费者（外部输入的声明节点
      自身即消费者——索引键集因此恒等于「ready gate 的本地探针全集」）；
    - ``edge.condition.artifact``：边的 target 是消费者（分支评估替它读
      文件）。
<<<<<<< HEAD
    索引键集即「这个名字会被本地探针/分支评估读取」的全集；hydration、
    分支裁决屏障与升级死名判定（``revision_diff.dropped_artifact_names``）
    都以本索引为准，不允许各自重遍历定义。
=======
    无生产者的声明名（外部输入）也出现在索引里——hydration 的恢复面与
    upgrade 保护计划的消费面都以本索引为准，不允许各自重遍历定义。
>>>>>>> 3f038f6d7 (feat(jobs)：workflow 升级 inherit 模式全量——revision diff/实现身份/保护计划/cleanup + 发布锁域 #645 #759)
    """
    index: dict[str, set[str]] = {}
    for key, node in definition.nodes.items():
        for name in node.inputs:
            index.setdefault(name, set()).add(key)
    for edge in definition.edges:
        if edge.condition is not None:
            index.setdefault(edge.condition.artifact, set()).add(edge.target)
    return {name: frozenset(consumers) for name, consumers in index.items()}


<<<<<<< HEAD
def consumer_edges(
    definition: WorkflowDefinition, *, skip_names: Iterable[str] = ()
) -> dict[str, list[str]]:
    """隐式消费边索引：producer key → 排序后的 consumer key 列表。

    ``skip_names``（#759 4.1）：排除经由这些名字的隐式边。判定「名 X 的
    consumer 是否保证在某 producer 之后执行」时，X 自己的隐式边正是被
    保留的启动对象 / manifest 回填所满足的等待——拿它当保证证据是循环
    论证，必须由调用方排除。当前是 ④ 层（upgrade-inherit）的前置 API，
    生产调用方随 ④ 落地。
    """
    skipped = set(skip_names)
    producers = artifact_producers(definition)
=======
def consumer_edges(definition: WorkflowDefinition) -> dict[str, list[str]]:
    """隐式消费边索引：producer key → 排序后的 consumer key 列表。"""
    producers: dict[str, set[str]] = {}
    for key, node in definition.nodes.items():
        for name in node.outputs:
            producers.setdefault(name, set()).add(key)
>>>>>>> 3f038f6d7 (feat(jobs)：workflow 升级 inherit 模式全量——revision diff/实现身份/保护计划/cleanup + 发布锁域 #645 #759)
    edges: dict[str, set[str]] = {key: set() for key in definition.nodes}
    for name, consumers in artifact_consumption_index(definition).items():
        for consumer in consumers:
            for producer in producers.get(name, ()):
                if producer != consumer:
                    edges[producer].add(consumer)
    return {key: sorted(targets) for key, targets in edges.items()}


<<<<<<< HEAD
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
=======
def dependency_children(definition: WorkflowDefinition) -> dict[str, list[str]]:
    """合并邻接表：显式边 ∪ 隐式消费边（key → 排序后的直接下游）。"""
>>>>>>> 3f038f6d7 (feat(jobs)：workflow 升级 inherit 模式全量——revision diff/实现身份/保护计划/cleanup + 发布锁域 #645 #759)
    children: dict[str, set[str]] = {key: set() for key in definition.nodes}
    for edge in definition.edges:
        children[edge.source].add(edge.target)
    for source, targets in consumer_edges(definition).items():
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
