"""旧快照中被移除/被删节点的产物名清理面（#645 codex 四轮 P1-2，#759 4.1/4.2）。

节点 output 从 ``old.json`` 改成 ``new.json``、或生产节点被删除时，
``stage_outputs`` 只按**新** definition 得暂存名——``old.json`` 不在
staged_artifact_names，清单行与本地文件全部保留：API 继续展示旧产物，
新图同名外部输入还会消费旧字节。本模块从旧快照
（job 的 ``workflow_definition_snapshot_json``，升级事务前仍是旧
revision）补出清理面：

- **移除的 output 名**：重置面节点在旧 definition 声明、新 definition
  不再声明的输出名（#759 4.2 起含 RMW 名——旧 RMW 产物在新图完全不再
  被引用时必须退役，不能永久留在 artifact API）；新图仍依赖且没有
  「保证先行」生产者的输入不清理（``unprotected_input_names``，#759
  4.1）——RMW 与纯外部输入都需要旧清单作为启动输入/回填来源；
- **被删节点的全部输出名 + 运行历史目录**：A4 的
  ``renamed_from_nodes`` 机制已处理「节点消失」的清单行（按新节点
  暂存名匹配），这里补「节点在但 output 名变了」与被删节点自身
  声明名的本地文件清理。

安全口径（与 ``staging_output_names`` 的 A3 语义对齐）：任何被**保留**
节点在新图中声明为输入或输出的名字不进清理面——那是继承节点的产物
或等待输入，清掉会把 completed 节点的清单行指向空文件。
"""

from __future__ import annotations

from dataclasses import dataclass

from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_consumption import dependency_children, walk_downstream


@dataclass(frozen=True)
class RemovedArtifactFace:
    """旧快照侧需要补清理的产物面（P1-2）。"""

    #: 被移除的输出名（重置节点的旧名 + 被删节点的全部输出名，#759 4.2 起含 RMW 名）。
    names: frozenset[str] = frozenset()
    #: 被删节点的 key（其 ``runs/<key>`` 执行历史目录一并暂存）。
    run_keys: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.names or self.run_keys)


def deleted_node_keys(
    old_definition: WorkflowDefinition | None, new_definition: WorkflowDefinition
) -> frozenset[str]:
    """被删节点身份 = 旧快照有、新图无的节点 key（#759 4.3）。

    与 ``removed_artifact_face`` 的产物名/runs 目录面同源（definition 差集），
    替代按 ``job_nodes`` 现存行推导——行缺失/多行的漂移场景口径一致。
    """
    if old_definition is None:
        return frozenset()
    return frozenset(old_definition.nodes) - frozenset(new_definition.nodes)


def unprotected_input_names(definition: WorkflowDefinition) -> frozenset[str]:
    """新图中「有声明输入面但无保证先行的生产者」的名字（#759 4.1）。

    clean 语义的全量清单清理与 ``removed_artifact_face`` 以此作保护集：
    外部输入（无生产者）与 RMW 启动名保留清单行/对象——删掉会让
    hydration/``restore_missing_inputs`` 无清单可回、节点永久等输入
    （#114 语义）。

    名 X 失去保护 ⇔ 新图中 X 的**每个** consumer 都保证在 X 的某个
    producer 之后执行：自举生产者（纯 producer，自身不消费 X，无需旧
    对象即可产出）经依赖邻接可达全部 consumer；RMW producer 自己被保证
    先行时加入自举集（内层 fixpoint）。

    #759 复审 P1（跨名互证）：判定「保证先行」时路径上经由的隐式消费边
    所跨的名字本身必须会在本次清理中缺席——经由 RMW 名（不暂存，旧文件
    存活）或受保护名（清单行保留）的隐式边不构成因果序：旧文件在场，
    consumer 的 ready gate 不会等其 producer 重跑。名字的保护状态因此
    互相依赖（X 的证明借 W 的边、W 的证明借 X 边即互证反例），判定是
    名集合上的**最小**不动点：从空集（无任何名的隐式边可作证据）向上
    迭代，一轮只用上一轮已证明缺席的名的隐式边（判 X 时 X 未入集，
    自己的隐式边天然被排除，防自证）；算子单调（缺席集越大可用边越
    多）、名集有限，至多 |names| 轮收敛；任何证不出的名保留保护——
    保守方向是多留清单行/旧对象，从不错删。
    """
    executable = definition.executable_nodes
    names = {name for node in executable.values() for name in node.inputs}
    cleanable: set[str] = set()
    while True:
        children = dependency_children(definition, skip_consumption_names=names - cleanable)
        grown = set(cleanable)
        for name in sorted(names - cleanable):
            producers = {key for key, node in executable.items() if name in node.outputs}
            sufficient = {key for key in producers if name not in executable[key].inputs}
            if not sufficient:
                # 外部输入 / 纯 RMW 互依赖：没有无需旧对象即可产出的生产者。
                continue
            while True:
                covered = walk_downstream(children, sufficient)
                expanded = sufficient | (producers & covered)
                if expanded == sufficient:
                    break
                sufficient = expanded
            consumers = {key for key, node in executable.items() if name in node.inputs}
            if consumers <= walk_downstream(children, sufficient):
                grown.add(name)
        if grown == cleanable:
            break
        cleanable = grown
    return frozenset(names - cleanable)


def removed_artifact_face(
    old_definition: WorkflowDefinition | None,
    new_definition: WorkflowDefinition,
    keep_keys: frozenset[str] | set[str],
    reset_keys: frozenset[str] | set[str],
) -> RemovedArtifactFace:
    """计算 P1-2 清理面：被移除 output 名 + 被删节点身份。

    ``keep_keys``/``reset_keys`` 按新 definition 的可执行节点划分（升级
    事务内的实际保留/重置面）。旧快照不可解析（None）时返回空面——
    该场景 plan 阶段已保守退化到全量重跑（clean 语义），不存在继承
    节点，重置面的新 outputs 走既有暂存路径（codex 五轮 P2-D 补齐了
    该分支的清单行清理）；旧快照节点缺失的行留给对象存储生命周期
    兜底（与 A4 的保守子集语义一致）。
    """
    if old_definition is None:
        return RemovedArtifactFace()
    names: set[str] = set()
    run_keys: set[str] = set()
    # 保留节点声明的输入 ∪ 输出：清理面不得触碰（A3 口径）。
    keep_io: set[str] = set()
    for key in keep_keys:
        node = new_definition.nodes.get(key)
        if node is not None:
            keep_io.update(node.inputs, node.outputs)
    # 保留节点的旧声明同样不碰（输入消费面在快照演进中保持同名）。
    for key in keep_keys:
        node = old_definition.nodes.get(key)
        if node is not None:
            keep_io.update(node.inputs, node.outputs)
    # 重置节点：旧输出 − 新声明面（outputs ∪ inputs）→ 被移除的名。#759
    # 4.2 起 RMW 名（inputs∩outputs）同样进候选：新图完全不再引用的旧
    # RMW 产物必须退役；仍被引用的名字由下方 unprotected_input_names
    # （4.1 的保证先行判定）与 keep_io 过滤兜底——删掉仍受保护的名会让
    # hydration/restore_missing_inputs 无清单可回、节点永久等待输入。
    for key in reset_keys:
        old_node = old_definition.nodes.get(key)
        if old_node is None:
            continue
        new_node = new_definition.nodes.get(key)
        declared = set() if new_node is None else set(new_node.outputs) | set(new_node.inputs)
        names.update(set(old_node.outputs) - declared)
    # 被删节点（旧有新无）：全部输出名（4.2 起含 RMW 名）+ 运行历史目录。
    for key, old_node in old_definition.nodes.items():
        if key not in new_definition.nodes:
            names.update(old_node.outputs)
            run_keys.add(key)
    names -= keep_io
    names -= unprotected_input_names(new_definition)
    # 与既有暂存面重叠的名（重置节点的新输出）不重复计——stage_outputs
    # 的正常路径已覆盖。
    names -= staging_output_names(new_definition, set(reset_keys))
    return RemovedArtifactFace(frozenset(names), frozenset(run_keys))
