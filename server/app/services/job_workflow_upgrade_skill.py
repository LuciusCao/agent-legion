"""skill 内容身份判定：执行时 commit vs 当前解析 commit（#645 codex 五轮 P1-A）。

agent 节点的执行内容除了 Agent 定义（``job_workflow_upgrade_impl`` 的
哈希维度）还有 skill 绑定（``effective_node_skill``：节点绑定优先，
``AgentDefinition.skill`` 的 legacy 兜底）。S5 只排除节点显式声明
``skill: latest`` 的面——legacy 兜底（节点不声明、Agent 定义带 skill，
ref 恒 latest）与显式具体 tag 都逃过 S5；仓库 HEAD 前进或
``make skills-lock`` 重解析 tag 后，节点定义与 Agent 定义哈希都不变，
inherit 保留按旧 skill commit 产出的产物而 dispatch 已会执行新 commit。

判定（``skill_excluded_nodes``）：执行记录的 skill 身份（请求行 manifest
的 ``skill_commit`` 完整 sha 优先、node_runs ``skill_version`` 的
``ref@commit12`` 前缀）与当前有效绑定经 ``resolve_skill_commit``（与
dispatch 同款 latest=live HEAD / tag=锁）解析出的 commit 比较，证明相等
才可继承；无记录、解析失败或不等 → 排除（保守重跑）。
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.skills.commit_cache import resolve_skill_commit
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.workflow_node_skill import effective_node_skill

logger = logging.getLogger(__name__)


def _effective_skill_binding(
    node: WorkflowNode, agent_definition: AgentDefinition | None
) -> tuple[str, str] | None:
    """agent 节点的有效 skill 绑定 ``(key, ref)``（dispatch 同款优先级）。

    ``effective_node_skill`` 在两侧皆空时抛 ValueError（dispatch 侧即节点
    失败）——这里返回 None 表示无 skill 面（由 P1-1 的哈希维度覆盖）。"""
    try:
        return effective_node_skill(node, agent_definition.skill if agent_definition else "")
    except ValueError:
        return None


def _executed_skill_commit(record: tuple[str, str, str, str]) -> str:
    """执行记录里的 skill commit：完整 sha 优先，回落 version 前缀。

    段 2（请求行）manifest 携带完整 ``skill_commit``（mark_done trim 保留
    该键）；段 1（node_runs）只有 ``skill_version = ref@commit12``——12 位
    前缀。前缀形态与当前解析的完整 sha 比前 12 位（git 短 sha 语义）。
    """
    skill_commit = record[2]
    if skill_commit:
        return skill_commit
    skill_version = record[3]
    if "@" in skill_version:
        return skill_version.rsplit("@", 1)[1]
    return ""


def _skill_commit_matches(
    skill_manager: Any,
    binding: tuple[str, str],
    record: tuple[str, str, str, str],
) -> bool:
    """执行时 skill commit 与当前解析 commit 是否证明一致。

    解析与 dispatch 同款（``resolve_skill_commit``：latest = live HEAD，
    具体 tag = DB 锁）；任何解析失败 → False（不可证明，保守重跑）。
    """
    try:
        current = resolve_skill_commit(skill_manager, binding[0], binding[1] or None)
    except Exception:
        # #204 broad-except audit: skill 解析面（DB 锁读取、git rev-parse、
        # 路径校验）的任何数据态失败都归入「不可证明」——保守重跑，不让
        # 升级 500。与 _published_catalog 的降级方向一致。
        logger.debug("skill commit resolution failed for %s", binding[0], exc_info=True)
        return False
    executed = _executed_skill_commit(record)
    if not executed:
        return False
    return current[: len(executed)] == executed


def skill_excluded_nodes(
    resolved_agents: dict[str, AgentDefinition],
    definition: WorkflowDefinition,
    executed: dict[str, tuple[str, str, str, str]],
    skill_manager: Any,
) -> frozenset[str]:
    """skill 内容身份不可证明/已漂移的 agent 节点集（codex 五轮 P1-A）。

    只看**有效绑定 skill** 的 agent 节点——无绑定的 agent 节点没有 skill
    面（dispatch 侧即节点失败，由 P1-1 的哈希维度覆盖）。节点显式声明
    ``skill: latest`` 的面已由 S5 排除（不重复计入，但在此重跑无害）。
    ``skill_manager`` 为 None（裸构造形态，测试直连）时跳过 skill 面——
    生产装配（job_service_factory）恒注入与 dispatch 同源的 manager，
    与 ``artifact_mutation=None`` 跳过本地暂存的同款降级约定。
    """
    if skill_manager is None:
        return frozenset()
    excluded: set[str] = set()
    for key, node in definition.executable_nodes.items():
        if node.node_type != "agent":
            continue
        binding = _effective_skill_binding(node, resolved_agents.get(key))
        if binding is None:
            continue
        record = executed.get(key)
        if record is None or not _skill_commit_matches(skill_manager, binding, record):
            excluded.add(key)
    return frozenset(excluded)
