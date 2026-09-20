"""upgrade 输入保护计划（#759 复审 P1-A）：reset-aware 的 liveness/freshness 双判定。

取代旧的 ``unprotected_input_names``（仅按新 definition 的名字集合最小不动点）：
旧判定在判名 X 时禁用 X 自己的隐式消费边，导致「纯 producer P(outputs=x) +
纯 consumer C(inputs=x)、无显式边、双方均 reset」的最小反例里 x 永远落入
keep 集——clean/全退化分支暂存了本地 x 字节却保留旧清单行，ready 前
hydration 立即复活旧字节，C 在 P 重跑前消费旧 revision 产物。已证伪的
前提「多保留是保守安全方向」：删多（外部输入/RMW 启动输入丢失）是永久等待，
留多（旧字节复活被新 revision 消费）是静默错误——两个方向都错，没有保守
方向可选，必须同时证明：

- **liveness**：删除后名字还会在新一轮执行中变得可用（consumer 不永久等待）。
  ``_available_names`` 最小不动点：未被作废的名字（外部输入、保留节点产物、
  纯 RMW 链——旧字节即权威）为种子；重置节点的全部输入可用 ⇒ 其输出可用。
  循环互依赖的生产者（px 等 y、py 等 x）证不出可运行 ⇒ liveness 失败。
- **freshness**：没有 consumer 会读到旧字节。非 RMW 名三面删除后「缺席即闸」
  ——ready gate 只探本地文件，名字缺席 ⇒ consumer 必然等到重置生产者重写
  （自己的隐式边在此是合法证据：删除是我们自己做的，缺席自证成立，前提是
  名字确实进了本次删除面）；RMW 附着名的旧文件不进暂存面（#114）而存活，
  缺席不成立 ⇒ 每个重置 consumer 必须有排序证据：经显式边 ∪ 经由「唯一
  生产者且本次会缺席」名字的隐式边，可达某个重置纯生产者的下游。
- **keep 侧同样要证**：名字被重置纯生产者作废后，保留旧字节给纯 consumer
  吃是静默错误；只有「未被作废」（外部输入/保留节点产物/纯 RMW 链）或
  「纯 consumer 全部被覆盖、仅未覆盖的 RMW consumer 需要启动输入」才可保留。

两方向任一不可证明 ⇒ 名字进 ``unprovable``，调用方 fail closed（upgrade
skipped/protection_unprovable，零副作用）——不猜保留也不猜删除。

纯函数：不触库、不触文件系统。输入是收敛后的实际保留/重置面（升级事务内
``stage_upgrade_reset_outputs`` 的收敛结果）与本次删除面（暂存名集合）。
"""

from __future__ import annotations

from dataclasses import dataclass

from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_consumption import artifact_consumption_index, walk_downstream


@dataclass(frozen=True)
class InputProtectionPlan:
    """一次升级的输入名三分区判定结果（reset-aware）。

    ``keep``：必须保留清单行/权威对象的名字（liveness 证明：外部输入、保留
    节点声明面、无重置纯生产者的名、以及有未覆盖 RMW consumer 的启动名）。
    ``clean``：可三面清理（本地文件/清单行/对象）的名字（liveness +
    freshness 均证明）。``unprovable``：两方向均不可证明——非空即 fail
    closed。``sweep``：``clean`` 中依赖「缺席即闸」的非 RMW 名——提交后需
    再扫一次本地文件（hydration 与提交交错可能在窗口内复活旧字节，见
    docs/architecture/execution-generation.md §5 残余面）。
    """

    keep: frozenset[str] = frozenset()
    clean: frozenset[str] = frozenset()
    unprovable: frozenset[str] = frozenset()
    sweep: frozenset[str] = frozenset()


class UpgradeProtectionUnprovableError(Exception):
    """保护计划存在不可证明名：fail closed（upgrade skipped，零副作用）。"""


def _name_indexes(
    definition: WorkflowDefinition,
) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """全图名字索引：producers（名 → 生产者集合）、consumers、RMW 附着名集。

    consumers 取 `workflow_consumption.artifact_consumption_index`（消费关系
    单一事实源）：node.inputs ∪ edge.condition.artifact 的 gated target——
    条件产物若漏出消费面，升级可能删除分支条件文件，分支评估静默走错
    分支（#759 复审增补 P1）。
    """
    executable = set(definition.executable_nodes)
    producers: dict[str, set[str]] = {}
    consumers: dict[str, set[str]] = {}
    rmw_attached: set[str] = set()
    for key, node in definition.executable_nodes.items():
        for name in node.outputs:
            producers.setdefault(name, set()).add(key)
        rmw_attached.update(set(node.inputs) & set(node.outputs))
    for name, consumer_keys in artifact_consumption_index(definition).items():
        in_scope = set(consumer_keys) & executable
        if in_scope:
            consumers[name] = in_scope
    return producers, consumers, rmw_attached


def _available_names(
    definition: WorkflowDefinition,
    reset: set[str],
    invalidated: set[str],
) -> set[str]:
    """liveness 不动点：新一轮执行中会变得可用的名字集合。

    种子 = 未被作废的名字（外部输入、保留节点产物、纯 RMW 链——旧字节即
    权威，本次升级不删除）；迭代：重置节点的全部输入可用 ⇒ 其输出可用。
    循环互依赖（px 等 y、py 等 x）的名字永远进不了集合 ⇒ liveness 不可证。
    """
    executable = definition.executable_nodes
    available = {
        name
        for node in executable.values()
        for name in set(node.inputs) | set(node.outputs)
        if name not in invalidated
    }
    while True:
        grown = set(available)
        for key in reset:
            node = executable[key]
            if set(node.inputs) <= available:
                grown.update(node.outputs)
        if grown == available:
            return available
        available = grown


def _ordering_children(
    definition: WorkflowDefinition,
    producers: dict[str, set[str]],
    consumers: dict[str, set[str]],
    absent: set[str],
) -> dict[str, set[str]]:
    """排序证据邻接表：显式边 ∪ 经由「唯一生产者且本次缺席」名字的隐式边。

    经由名 z 的隐式边 p→c 成立（c 保证在 p 之后执行）当且仅当 c 会等 z
    缺席到被重写：z ∈ ``absent``（三面删除 ⇒ ready gate 探不到）且 z 只有
    唯一生产者 p——多生产者时 c 可能只等到另一个生产者的重写，不构成
    「在 p 之后」的证据。
    """
    executable = definition.executable_nodes
    children: dict[str, set[str]] = {key: set() for key in executable}
    for edge in definition.edges:
        if edge.source in executable and edge.target in executable:
            children[edge.source].add(edge.target)
    for name in absent:
        name_producers = producers.get(name, set())
        if len(name_producers) != 1:
            continue
        producer = next(iter(name_producers))
        for consumer in consumers.get(name, set()):
            if consumer != producer:
                children[producer].add(consumer)
    return children


def input_protection_plan(
    definition: WorkflowDefinition,
    *,
    keep_nodes: frozenset[str] | set[str],
    reset_nodes: frozenset[str] | set[str],
    staged_names: frozenset[str] | set[str],
) -> InputProtectionPlan:
    """计算 reset-aware 的输入保护计划（keep / clean / unprovable 三分区）。

    ``keep_nodes``/``reset_nodes`` 是收敛后的实际保留/重置面（事务内
    ``job_nodes`` 状态收敛 + 同名生产者闭包之后），``staged_names`` 是本次
    删除面（``staging_output_names`` 对重置面的计算结果——缺席判定的证据
    必须以实际删除为前提）。只判定新图中被声明为 input 的名字；未被消费的
    名字不进 keep 集（清理面无需保护它们）。
    """
    executable = definition.executable_nodes
    keep_keys = set(keep_nodes) & set(executable)
    reset = set(reset_nodes) & set(executable)
    producers, consumers, rmw_attached = _name_indexes(definition)
    staged = set(staged_names)

    def _invalidated(name: str) -> bool:
        """存在重置纯生产者（不消费该名）⇒ 旧字节被本次升级作废。"""
        return any(
            name in executable[key].outputs and name not in executable[key].inputs for key in reset
        )

    input_names = set(consumers)
    invalidated = {name for name in input_names if _invalidated(name)}
    available = _available_names(definition, reset, invalidated)
    # 缺席即闸的证据集：被作废、会重生成、非 RMW 附着（旧文件会被暂存走）、
    # 且确实在本次删除面内（不在删除面 ⇒ 缺席不可证 ⇒ 相关名 fail closed）。
    absent = {
        name
        for name in staged
        if name in invalidated and name in available and name not in rmw_attached
    }
    children = _ordering_children(definition, producers, consumers, absent)
    # 保留节点声明的输入 ∪ 输出：A3 口径一律不碰（保留节点的有效产物）。
    kept_declared: set[str] = set()
    for key in keep_keys:
        kept_declared.update(executable[key].inputs, executable[key].outputs)

    keep: set[str] = set()
    clean: set[str] = set()
    unprovable: set[str] = set()
    for name in sorted(input_names):
        if name in kept_declared or name not in invalidated:
            # A3 保留面 / 外部输入 / 纯 RMW 链：旧字节即权威，保留才正确。
            keep.add(name)
            continue
        if name not in available:
            # 循环互依赖：重置生产者永远跑不起来——删则永久等待，留则旧
            # 字节复活被消费，两方向均不可证明。
            unprovable.add(name)
            continue
        if name not in rmw_attached:
            if name in staged:
                # 三面删除 ⇒ 缺席即闸：consumer 必然等到重置生产者重写。
                clean.add(name)
            else:
                unprovable.add(name)
            continue
        # RMW 附着名：旧文件不进暂存面而存活，缺席不成立 ⇒ 每个重置
        # consumer 都需排序证据（保证在某重置纯生产者之后执行）。
        sufficient = {
            key
            for key in producers.get(name, set())
            if key in reset and name not in executable[key].inputs
        }
        covered = walk_downstream(children, sufficient)
        reset_consumers = consumers.get(name, set()) & reset
        pure_uncovered = {
            key for key in reset_consumers if name not in executable[key].outputs
        } - covered
        if pure_uncovered:
            # 留：纯 consumer 经存活的旧文件吃到旧字节；删：RMW 面不暂存
            # 文件同样存活——两方向都错 ⇒ fail closed。
            unprovable.add(name)
            continue
        rmw_uncovered = {
            key for key in reset_consumers if name in executable[key].outputs
        } - covered
        if rmw_uncovered:
            # RMW consumer 的启动输入必须保留（#114 语义）；被覆盖的纯
            # consumer 仍经排序证据等新字节，保留无害。
            keep.add(name)
        else:
            clean.add(name)
    sweep = frozenset(name for name in clean if name not in rmw_attached)
    return InputProtectionPlan(frozenset(keep), frozenset(clean), frozenset(unprovable), sweep)
