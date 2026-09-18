"""Inherit 判定的传播闭包与种子收集（issue #645，702 传播闭包重构）。

判定基准：节点 N 可继承 ⟺ 其全部输入（上游产物 + 配置 + 实现）可证明
与产出当前产物的执行一致，且 N 不在重跑闭包里。本模块把这个基准拆成
**种子收集**（``collect_change_seeds``，纯局部判定）与**双通道传播闭包**
（``rerun_closure``，结构依赖 + 存储键依赖）两层：

- 种子（S1–S5）只看本节点的新旧快照与执行记录，不含任何上游信息——
  上游维度完全交给闭包（"全部上游都不在重跑闭包里"直接定义，取代
  旧实现把祖先变化压进 per-node 哈希的链式压缩）；
- 闭包通道 A（边）：种子节点的全部新图下游（``downstream_nodes``，
  条件边含在 children map）；通道 B（名字）：与重置面共享输出名（含 RMW）的
  候选一起重跑（``shared_name_rerun_closure`` 的 fixpoint）。

任何重跑原因（定义 diff、配置漂移、实现身份、可达性、排除规则、名字
共享）接入时只要进种子集，就自动获得全下游传播——新增判定源不可能
「忘了传播」，用户反例（种子重跑、下游的旧侧基准不变、下游被错误
继承）从结构上不可复现。

``compute_inherit_reset_nodes``（diff 层）是本模块的既有签名 wrapper：
种子 → 闭包的组合，40 个既有用例断言零改动承重（等价性的直接回归网）。
纯函数：不触库、不触文件系统（实现身份与可达性种子由调用方先解析）。
"""

from __future__ import annotations

from dataclasses import asdict

from server.app.services.job_artifact_staging_scope import shared_name_rerun_closure
from server.app.services.job_workflow_upgrade_diff import (
    _frozen_config_section,
    _incoming_edges_map,
    node_definition_hash,
    node_is_inherit_excluded,
)
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_branching import downstream_nodes


def collect_change_seeds(
    old_definition: WorkflowDefinition,
    old_frozen_config_json: str | None,
    new_definition: WorkflowDefinition,
    new_frozen_config_json: str | None,
    implementation_excluded: frozenset[str] | set[str] = frozenset(),
) -> set[str]:
    """新图可执行节点中的局部变更种子集（S1–S5，不含上游传播）。

      - S1 定义种子：key 不在旧快照可执行集（新增），或节点定义归一化
        哈希新旧不等（label 等展示字段仍排除）；
      - S2 配置种子：frozen config 段新旧不等（旧侧 = job 存量冻结值，
        RUN-FREEZE-001 同基）；
      - S3 入边种子：入边声明（source + when 条件，含序）新旧不等——
        调度语义（分支裁剪）变化等价于输入面变化；
      - S4 实现身份种子：``implementation_excluded``（plan 层解析好的
        「实现身份不可证明或已漂移」+ Agent 定义侧 runtime_mutable 键，
        ``job_workflow_upgrade_impl``）；
      - S5 排除规则种子：``node_is_inherit_excluded``（skill:latest /
        分片 / 审批门 / 节点自声明 runtime_mutable 键）。

    旧快照中已删除的节点不进种子（job_nodes 由 mutation 按新定义重建，
    只保留新节点集）。
    """
    old_executable = old_definition.executable_nodes
    old_incoming = _incoming_edges_map(old_definition)
    new_incoming = _incoming_edges_map(new_definition)

    def _edge_signature(edges: list) -> list[dict[str, object]]:
        return [
            {"source": edge.source, "condition": asdict(edge.condition)}
            if edge.condition is not None
            else {"source": edge.source}
            for edge in edges
        ]

    seeds: set[str] = set(implementation_excluded)
    for key, node in new_definition.executable_nodes.items():
        old_node = old_executable.get(key)
        if old_node is None or node_definition_hash(old_node) != node_definition_hash(node):
            # S1：新增节点或定义哈希漂移。
            seeds.add(key)
            continue
        if _frozen_config_section(old_frozen_config_json, key) != _frozen_config_section(
            new_frozen_config_json, key
        ):
            # S2：冻结 config 段演进（workspace 配置漂移）。
            seeds.add(key)
            continue
        if _edge_signature(old_incoming.get(key, [])) != _edge_signature(new_incoming.get(key, [])):
            # S3：入边声明变化（含 when 条件与声明序，序敏感 = 保守方向）。
            seeds.add(key)
            continue
        if node_is_inherit_excluded(node):
            # S5：排除规则（skill:latest / 分片 / 审批门 / 自声明 mutable）。
            seeds.add(key)
    return seeds


def rerun_closure(definition: WorkflowDefinition, seeds: set[str]) -> set[str]:
    """种子集的重跑闭包（通道 A 边传播 + 通道 B 名字传播）。

    通道 A：每个种子的全部新图下游（``downstream_nodes`` 全边 children
    map，条件边含在内；自带 seen 防环——新图经 loader _validate_acyclic，
    环防御是兜底）。通道 B：与重置面共享输出名（含 RMW）的候选一起重跑
    （``shared_name_rerun_closure`` 的 fixpoint，每次排除扩大重置面，
    新排除节点的下游也并入）。返回值限于新图可执行节点。

    这是**唯一**的重置面来源：任何种子自动获得全下游传播。
    """
    executable = definition.executable_nodes
    reset = {key for key in seeds if key in executable}
    for key in list(reset):
        reset.update(downstream_nodes(definition, key))
    reset &= set(executable)
    excluded = shared_name_rerun_closure(definition, frozenset(executable) - reset, reset)
    return reset | excluded
