"""重置闭包的图收敛（#759/codex #776 复审 P1；预算拆分自 staging_scope）。

同名纯输出不能跨重置边界拆分：对象键 ``jobs/<ws>/<job>/<name>`` 不含
node 身份——重置节点与面外节点共享 output 名时，staging 的 A3 同名
排除会让该名既不暂存也不删行；重置节点的新 attempt 若没写该文件，
``_check_outputs`` 只查存在性，会把面外节点遗留的旧字节当本次输出
（静默串用）；写出则面外 completed 节点的清单行指向别人的内容。因此
rerun / rework / run-to / upgrade 的重置面一律经本模块收敛同名生产者
（含 RMW）及其下游，A3 排除降为文件系统安全兜底。保守方向：多跑、
绝不串数据。

调用方必须把收敛后的同一集合喂给 ``stage_outputs`` 与 mutation
（重置集 ≡ 暂存集，stage_outputs 不做任何图遍历）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.workflow_consumption import (
    dependency_children,
    dependency_downstream,
    walk_downstream,
)


def _producer_outputs(definition: WorkflowDefinition, key: str) -> set[str]:
    return set(definition.nodes[key].outputs)


def shared_name_rerun_closure(
    definition: WorkflowDefinition,
    keep: frozenset[str],
    reset_face: set[str],
) -> set[str]:
    """Keep-set nodes that must rerun because they share an output name
    with the reset face, plus their downstream closure (codex P1-3).

    Same-name producers cannot be split across the inherit/reset boundary:
    whichever writes last owns the shared object key, and the loser's
    manifest row points at foreign content. Returns the subset of ``keep``
    to move into the reset face, computed to a fixpoint — each exclusion
    joins the face and may cascade through further name sharing or
    downstream dependencies (an excluded node reruns, so its old outputs are
    semantically replaced and its kept descendants cannot inherit them).
    The cascade walks the merged adjacency (explicit edges ∪ implicit
    consumption edges, #759): a consumer with no declared edge still reads
    the excluded node's outputs. Conservative direction: extra reruns, never
    crossed data. Trigger names are ALL outputs of the face (upgrade 语义
    含 RMW——保留/重置边界的 RMW 同名同样不能拆分）；rerun 一族的
    纯输出触发变体见 ``shared_name_expanded_reset``。
    """
    return _shared_name_closure(definition, keep, reset_face, pure_trigger_only=False)


def _shared_name_closure(
    definition: WorkflowDefinition,
    keep: frozenset[str],
    reset_face: set[str],
    *,
    pure_trigger_only: bool,
) -> set[str]:
    excluded: set[str] = set()
    children = dependency_children(definition)
    while True:
        face = reset_face | excluded
        face_names: set[str] = set()
        for key in face:
            node = definition.nodes[key]
            # pure_trigger_only（rerun 一族，#114）：重置节点 RMW 名的旧值
            # 复用是设计语义（读旧值→改写），不触发收敛；只有纯输出名才有
            # 「本次没写就吃面外旧字节」的串用面。
            face_names.update(
                set(node.outputs) - set(node.inputs) if pure_trigger_only else node.outputs
            )
        if not face_names:
            return excluded
        newly = {key for key in keep - excluded if _producer_outputs(definition, key) & face_names}
        if not newly:
            return excluded
        excluded |= newly
        excluded.update(walk_downstream(children, newly) & (set(keep) - excluded))


def shared_name_expanded_reset(
    definition: WorkflowDefinition, reset_nodes: Iterable[str]
) -> set[str]:
    """重置集 ∪ 同名生产者收敛（codex #776 复审 P1，模块 docstring）。

    触发名只取重置面的**纯输出**（outputs − inputs）：RMW 名的旧值复用
    是 #114 设计语义，不触发收敛；同名拉入仍按全部 outputs 判定（面外
    RMW 节点同样共享对象键，必须一起重跑）。
    """
    face = set(reset_nodes)
    keep = frozenset(set(definition.executable_nodes) - face)
    return face | _shared_name_closure(definition, keep, face, pure_trigger_only=True)


def rerun_reset_closure(definition: WorkflowDefinition, seeds: Iterable[str]) -> set[str]:
    """重置闭包：种子 ∪ 合并下游 ∪ 同名生产者收敛（codex #776 复审 P1）。

    rerun / rework / run-to-with-start 的统一重置面入口：种子沿合并下游
    （显式边 ∪ 隐式消费边）传播后，同名生产者及其下游一并进重置面。
    """
    face: set[str] = set().union(
        seeds, *(dependency_downstream(definition, seed) for seed in seeds)
    )
    return shared_name_expanded_reset(definition, face)


def run_to_reset_nodes(
    definition: WorkflowDefinition,
    closure: frozenset[str],
    current_statuses: Mapping[str, Any],
) -> list[str]:
    """run-to（无起始节点）的锁内重置集：closure ∩ 非 completed ∪ 同名收敛。

    状态过滤必须在 mutation 锁内用当前读数（TOCTOU）；同名生产者收敛
    是纯图计算。闭包内的同名生产者翻 pending 本轮重跑、闭包外的翻
    stale 失效，由 ``apply_run_to`` 按闭包切分。
    """
    reset = {key for key in closure if current_statuses.get(key) != "completed"}
    return sorted(shared_name_expanded_reset(definition, reset))
