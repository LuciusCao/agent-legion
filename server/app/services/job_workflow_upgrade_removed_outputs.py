"""旧快照中被移除/被删节点的产物名清理面（#645 codex 四轮 P1-2，#759 4.1/4.2）。

节点 output 从 ``old.json`` 改成 ``new.json``、或生产节点被删除时，
``stage_outputs`` 只按**新** definition 得暂存名——``old.json`` 不在
staged_artifact_names，清单行与本地文件全部保留：API 继续展示旧产物，
新图同名外部输入还会消费旧字节。本模块从旧快照
（job 的 ``workflow_definition_snapshot_json``，升级事务前仍是旧
revision）补出清理面：

- **移除的 output 名**：重置面节点在旧 definition 声明、新 definition
  不再声明的输出名（#759 4.2 起含 RMW 名——旧 RMW 产物在新图完全不再
  被引用时必须退役，不能永久留在 artifact API）；新图仍依赖且保护计划
  判定保留的输入名不清理（``protected_names``，#759 复审 P1-A 起由
  ``job_workflow_upgrade_protection.input_protection_plan`` 给出——
  RMW 与纯外部输入都需要旧清单作为启动输入/回填来源）；
- **被删节点的全部输出名 + 运行历史目录**：A4 的
  ``renamed_from_nodes`` 机制已处理「节点消失」的清单行（按新节点
  暂存名匹配），这里补「节点在但 output 名变了」与被删节点自身
  声明名的本地文件清理；删除面比较 ``executable_nodes``——同 key 从
  可执行转为 ``type: start`` 按删除旧执行节点处理（codex #776 复审
  P2 R5），其旧 outputs/run 目录/queued 请求不逃逸清理。

安全口径（与 ``staging_output_names`` 的 A3 语义对齐）：任何被**保留**
节点在新图中声明为输入或输出的名字不进清理面——那是继承节点的产物
或等待输入，清掉会把 completed 节点的清单行指向空文件。
"""

from __future__ import annotations

from dataclasses import dataclass

from server.app.services.job_artifact_staging_scope import staging_output_names
from server.app.workflows.definition import WorkflowDefinition


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
    """被删节点身份 = 旧快照有、新图无的节点 key ∪ 转为 start 的旧执行节点。

    与 ``removed_artifact_face`` 的产物名/runs 目录面同源（definition 差集），
    替代按 ``job_nodes`` 现存行推导——行缺失/多行的漂移场景口径一致。
    codex #776 复审 P2（R5）：同 key 节点从可执行转为 ``type: start`` 时
    全节点 key 差集识别不到（start 节点仍在 ``nodes`` 里），但它已不在新图
    执行面——旧 outputs/运行历史/queued 请求必须按删除处理。对称面
    （start→可执行）由新增节点种子天然覆盖（S1）。
    """
    if old_definition is None:
        return frozenset()
    return (frozenset(old_definition.nodes) - frozenset(new_definition.nodes)) | (
        frozenset(old_definition.executable_nodes) - frozenset(new_definition.executable_nodes)
    )


def removed_artifact_face(
    old_definition: WorkflowDefinition | None,
    new_definition: WorkflowDefinition,
    keep_keys: frozenset[str] | set[str],
    reset_keys: frozenset[str] | set[str],
    *,
    protected_names: frozenset[str] | set[str],
) -> RemovedArtifactFace:
    """计算 P1-2 清理面：被移除 output 名 + 被删节点身份。

    ``keep_keys``/``reset_keys`` 按新 definition 的可执行节点划分（升级
    事务内的实际保留/重置面）。``protected_names``（#759 复审 P1-A）是
    reset-aware 保护计划的 keep 集（``input_protection_plan``，调用方在
    同一收敛面上算出；unprovable 非空时调用方已 fail closed，不会走到
    这里）。旧快照不可解析（None）时返回空面——该场景 plan 阶段已保守
    退化到全量重跑（clean 语义），不存在继承节点，重置面的新 outputs 走
    既有暂存路径（codex 五轮 P2-D 补齐了该分支的清单行清理）；旧快照
    节点缺失的行留给对象存储生命周期兜底（与 A4 的保守子集语义一致）。
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
    # RMW 产物必须退役；仍被引用的名字由下方 protected_names（P1-A 的
    # reset-aware liveness/freshness 判定）与 keep_io 过滤兜底——删掉仍受
    # 保护的名会让 hydration/restore_missing_inputs 无清单可回、节点永久
    # 等待输入。
    for key in reset_keys:
        old_node = old_definition.nodes.get(key)
        if old_node is None:
            continue
        new_node = new_definition.nodes.get(key)
        declared = set() if new_node is None else set(new_node.outputs) | set(new_node.inputs)
        names.update(set(old_node.outputs) - declared)
    # 被删节点（旧有新无，含 executable→start 转换——codex #776 复审 P2
    # （R5），与 deleted_node_keys 同口径）：全部输出名（4.2 起含 RMW 名）
    # + 运行历史目录。
    for key in sorted(deleted_node_keys(old_definition, new_definition)):
        old_node = old_definition.nodes[key]
        names.update(old_node.outputs)
        run_keys.add(key)
    names -= keep_io
    names -= set(protected_names)
    # 与既有暂存面重叠的名（重置节点的新输出）不重复计——stage_outputs
    # 的正常路径已覆盖。
    names -= staging_output_names(new_definition, set(reset_keys))
    return RemovedArtifactFace(frozenset(names), frozenset(run_keys))
