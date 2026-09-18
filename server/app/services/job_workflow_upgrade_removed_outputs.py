"""旧快照中被移除/被删节点的产物名清理面（#645 codex 四轮 P1-2）。

节点 output 从 ``old.json`` 改成 ``new.json``、或生产节点被删除时，
``stage_outputs`` 只按**新** definition 得暂存名——``old.json`` 不在
staged_artifact_names，清单行与本地文件全部保留：API 继续展示旧产物，
新图同名外部输入还会消费旧字节。本模块从旧快照
（job 的 ``workflow_definition_snapshot_json``，升级事务前仍是旧
revision）补出清理面：

- **移除的 output 名**：重置面节点在旧 definition 声明、新 definition
  不再声明的纯输出名（outputs − inputs，RMW 名不清理：#114 同款——
  移除一个无人再生产的输入会饿死下游）；
- **被删节点的全部纯输出名 + 运行历史目录**：A4 的
  ``renamed_from_nodes`` 机制已处理「节点消失」的清单行（按新节点
  暂存名匹配），这里补「节点在但 output 名变了」与被删节点自身
  声明名的本地文件清理。

安全口径（与 ``staging_output_names`` 的 A3 语义对齐）：任何被**保留**
节点在新图中声明为输入或输出的名字不进清理面——那是继承节点的产物
或等待输入，清掉会把 completed 节点的清单行指向空文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.schema import WorkflowNode


@dataclass(frozen=True)
class RemovedArtifactFace:
    """旧快照侧需要补清理的产物面（P1-2）。"""

    #: 被移除的纯输出名（重置节点的旧名 + 被删节点的全部纯输出名）。
    names: frozenset[str] = frozenset()
    #: 被删节点的 key（其 ``runs/<key>`` 执行历史目录一并暂存）。
    run_keys: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.names or self.run_keys)

    @property
    def is_empty(self) -> bool:
        return not (self.names or self.run_keys)


@dataclass
class _FaceBuilder:
    names: set[str] = field(default_factory=set)
    run_keys: set[str] = field(default_factory=set)


def _pure_outputs(node: WorkflowNode) -> set[str]:
    return set(node.outputs) - set(node.inputs)


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
    节点，重置面的新 outputs 走既有暂存路径；旧快照节点缺失的行留给
    对象存储生命周期兜底（与 A4 的保守子集语义一致）。
    """
    if old_definition is None:
        return RemovedArtifactFace()
    builder = _FaceBuilder()
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
    # 重置节点：旧纯输出 − 新纯输出 → 被移除的名。
    for key in reset_keys:
        old_node = old_definition.nodes.get(key)
        if old_node is None:
            continue
        new_node = new_definition.nodes.get(key)
        removed = _pure_outputs(old_node) - (
            _pure_outputs(new_node) if new_node is not None else set()
        )
        builder.names.update(removed)
    # 被删节点（旧有新无）：全部纯输出名 + 运行历史目录。
    for key, old_node in old_definition.nodes.items():
        if key not in new_definition.nodes:
            builder.names.update(_pure_outputs(old_node))
            builder.run_keys.add(key)
    builder.names -= keep_io
    # 与既有暂存面重叠的名（重置节点的新输出）不重复计——stage_outputs
    # 的正常路径已覆盖。
    builder.names -= staging_output_names(new_definition, set(reset_keys))
    return RemovedArtifactFace(frozenset(builder.names), frozenset(builder.run_keys))
