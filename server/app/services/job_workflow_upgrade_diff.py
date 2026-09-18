"""Per-node upgrade diff comparators for the inherit upgrade mode (issue #645).

``clean`` 模式的 upgrade 把全部 job_nodes 无条件重置 pending 全量重跑；
``inherit`` 模式只重跑「真正变了」的子图。本模块提供种子判定所需的
**单侧纯比较器**：节点定义归一化哈希、冻结 config 段、入边声明、排除
规则。种子的编排（新旧双侧比较）在 ``job_workflow_upgrade_propagation``
（种子集 + 传播闭包），``compute_inherit_reset_nodes`` 是它的既有签名
wrapper。

比较器语义：

1. 节点定义归一化：从 revision 快照反序列化出的 ``WorkflowNode`` 中取出
   影响执行的每个字段（capability / node_type / after / inputs / outputs /
   terminal / execution / config / config_schema / skill / tools / shard /
   reduce / accepted_item_types），dataclass 序列化 + 顶层排序键 JSON。
   **label 等纯展示字段刻意排除**——只改显示名不该烧掉已完成的产物。
   进出节点（inputs/outputs/terminal）本身也在定义哈希里：改上游产出名
   等于改契约，下游与自身都必须重跑。
2. 冻结 config 段：新侧是 upgrade 前 re-freeze 的 ``frozen_config_json``
   （schema 默认值、节点 config、workspace 覆盖），旧侧优先用 job 的
   存量冻结值（intake 冻结，产物按它产出）；任何一侧的执行输入变化都
   触发重跑。workspace 层配置变化体现在新侧 freeze 里，与 job 存量冻结
   值的差异即「配置演进」，受影响节点重跑。

排除规则（issue 边界 + codex 四轮，一律不继承、永远重跑）：

- ``skill: latest`` 节点：HEAD 漂移永不入锁（#322），diff 无法观测其
  内容变化，参与继承会掩盖 skill 更新；
- 分片节点（声明 ``shard:`` / ``reduce:``）：``node_shards`` 行级状态
  是 fan-out 执行的一部分，继承聚合状态无法安全重放；
- 审批门节点（``type: approval``）：人工决策语义（approve/rework）不
  可从定义 diff 推导，重置回 ``awaiting_approval`` 前的 pending 由
  人工重新决策；
- 含 ``runtime_mutable`` config 键的节点（codex 四轮 P1-3）：这些键
  每次 dispatch 现场重解析（CONFIG-RUNTIME-MUTABLE-001），frozen 段
  只是 intake 时刻的快照——override intake 后改 B、节点按 B 完成、
  升级前改回 A 时新旧 frozen 哈希相等，继承的却是按 B 产出的产物。
  执行侧虽有 ``node_runs.config_snapshot_json`` 审计，但它是按 run 行
  的全量 config 快照（多 attempt / 多 shard 各一行、三条执行路径的
  manifest 键投影各异），拿它做「继承节点该次产出的实际配置」的比较
  无法证明完备——保守排除，含此类键的节点恒重跑（排除面见
  ``runtime_mutable_excluded_nodes``）；**agent 节点的有效 schema 主体
  来自 Agent 定义**（节点自声明被定义覆盖），定义侧键由 plan 层
  ``job_workflow_upgrade_impl`` 解析 AgentDefinition.config_schema 后
  并入执行面排除集（codex 四轮复审 HIGH-2），本模块的节点自声明判定
  只覆盖 code 节点面；
- 实现身份不可证明的节点（codex 四轮 P1-1）：普通 job 不 pin 版本，
  dispatch 现场解析 workspace 当前 published 的 node_code（code 节点）
  或 Agent 定义（agent 节点）——执行时身份（v84 起
  ``node_runs.agent_definition_hash``，请求行 fallback）与当前
  published 身份无法证明相等（记录被 retention 清扫、实现未发布）时，
  旧产物按哪份实现产出不可知 → 恒重跑（plan 层解析后经
  ``implementation_excluded`` 种子传入）。

本模块是纯函数：不触库、不触文件系统。实现身份与 runtime_mutable 的
解析由调用方（plan 层）先完成并传入，本模块只消费排除集。可达性退化
（产物缺失退化为重跑）在服务层 ``job_workflow_upgrade.py`` 判定，与
本模块解耦。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from server.app.services.node_config_runtime import runtime_mutable_keys
from server.app.skills.config import LATEST_REF
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.schema import WorkflowEdge, WorkflowNode

#: 展示专用字段：从节点定义哈希中剔除（改 label 不触发重跑）。
_DISPLAY_ONLY_FIELDS = ("key", "label")

#: 审批门节点类型常量（与 ``workflows/approval_node`` 一致；避免循环导入）。
_APPROVAL_NODE_TYPE = "approval"


def _stable_json(value: Any) -> str:
    """排序键的紧凑 JSON：哈希前的唯一归一化形式。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def node_signature(node: WorkflowNode) -> dict[str, Any]:
    """一个节点的执行语义视图：全部影响执行的字段，无展示字段。"""
    payload = asdict(node)
    for field in _DISPLAY_ONLY_FIELDS:
        payload.pop(field, None)
    return payload


def node_definition_hash(node: WorkflowNode) -> str:
    """归一化后的节点定义哈希。"""
    return hashlib.sha256(_stable_json(node_signature(node)).encode("utf-8")).hexdigest()


def _frozen_config_section(frozen_config_json: str | None, node_key: str) -> dict[str, Any]:
    """该节点的冻结 config 段；无冻结或段缺失即空（与 dispatch 的空段一致）。

    损坏/非对象的冻结值按空段参与比较：dispatch 侧 ``parse_object``
    对同样内容也是同一降级（RUN-FREEZE-001），两侧语义对齐。
    """
    if not frozen_config_json:
        return {}
    try:
        payload = json.loads(frozen_config_json)
    except (TypeError, ValueError):
        return {}
    section = payload.get(node_key) if isinstance(payload, dict) else None
    return section if isinstance(section, dict) else {}


def _has_runtime_mutable_keys(node: WorkflowNode) -> bool:
    """节点自声明 config_schema 是否含 ``runtime_mutable: true`` 业务键。

    保留执行键由 loader 禁止重声明、``runtime_mutable_keys`` 本身剔除，
    节点声明即完备口径。Agent 节点的 schema 主体在 Agent 定义里（节点
    自声明被定义覆盖）——定义侧键由 plan 层
    ``job_workflow_upgrade_impl._agent_definition_mutable_nodes`` 解析
    AgentDefinition.config_schema 并入排除集（复审 HIGH-2），本函数只
    覆盖节点自声明面。
    """
    return bool(runtime_mutable_keys(node.config_schema or {}))


def runtime_mutable_excluded_nodes(definition: WorkflowDefinition) -> set[str]:
    """含 runtime_mutable 键的可执行节点集（codex 四轮 P1-3 排除面）。"""
    return {
        key for key, node in definition.executable_nodes.items() if _has_runtime_mutable_keys(node)
    }


def node_is_inherit_excluded(node: WorkflowNode) -> bool:
    """该节点不参与继承（issue #645 边界）：skill:latest / 分片 / 审批门
    / 含 runtime_mutable config 键。

    实现身份不可证明的排除（P1-1）不在此处：它需要执行记录与当前
    published 的解析结果，由 plan 层算好传进
    ``compute_inherit_reset_nodes``。
    """
    if node.skill is not None and (node.skill.ref or LATEST_REF) == LATEST_REF:
        return True
    if node.shard is not None or node.reduce is not None:
        return True
    if node.node_type == _APPROVAL_NODE_TYPE:
        return True
    return _has_runtime_mutable_keys(node)


def _incoming_edges_map(definition: WorkflowDefinition) -> dict[str, list[WorkflowEdge]]:
    """node_key → 入边列表（含条件声明）：边的增删与 when 条件的变化
    改变节点的调度语义（分支裁剪），是独立的入边种子判定输入。"""
    incoming: dict[str, list[WorkflowEdge]] = {key: [] for key in definition.nodes}
    for edge in definition.edges:
        incoming.setdefault(edge.target, []).append(edge)
    return incoming


def compute_inherit_reset_nodes(
    old_definition: WorkflowDefinition,
    old_frozen_config_json: str | None,
    new_definition: WorkflowDefinition,
    new_frozen_config_json: str | None,
    implementation_excluded: frozenset[str] | set[str] = frozenset(),
) -> set[str]:
    """新 revision 下需要重跑的节点集（种子 + 双通道传播闭包）。

    702 传播闭包重构后本函数是 ``job_workflow_upgrade_propagation`` 的
    thin wrapper（签名不变，既有 40 用例断言零改动承重）：
    ``collect_change_seeds``（S1 定义 / S2 config / S3 入边 / S4 实现身份
    / S5 排除规则的纯局部种子）→ ``rerun_closure``（通道 A 全下游边传播
    + 通道 B 同名生产者 fixpoint）。上游一致性不再由 per-node 哈希链
    间接证明，而由「全部上游都不在重跑闭包里」直接定义。

      - 节点在旧快照中不存在（新增节点）→ 变更；
      - 节点在新 revision 中不存在（删除节点）→ 不在结果里（job_nodes
        会被 mutation 重建，只保留新定义的节点集）；
      - 种子（定义/config/入边/排除/实现身份任一变化）→ 变更 + 下游闭包。
    """
    from server.app.services.job_workflow_upgrade_propagation import (
        collect_change_seeds,
        rerun_closure,
    )

    seeds = collect_change_seeds(
        old_definition,
        old_frozen_config_json,
        new_definition,
        new_frozen_config_json,
        implementation_excluded,
    )
    return rerun_closure(new_definition, seeds)
