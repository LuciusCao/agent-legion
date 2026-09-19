"""skill 内容身份判定：执行时 commit vs DB 锁文档权威值（#645 codex 五轮 P1-A；#759 P1 收紧）。

agent 节点的执行内容除了 Agent 定义（``job_workflow_upgrade_impl`` 的
哈希维度）还有 skill 绑定（``effective_node_skill``：节点绑定优先，
``AgentDefinition.skill`` 的 legacy 兜底）。S5 只排除节点显式声明
``skill: latest`` 的面——legacy 兜底（节点不声明、Agent 定义带 skill，
ref 恒 latest）与显式具体 tag 都逃过 S5；仓库 HEAD 前进或
``make skills-lock`` 重解析 tag 后，节点定义与 Agent 定义哈希都不变，
inherit 保留按旧 skill commit 产出的产物而 dispatch 已会执行新 commit。

判定（``skill_excluded_nodes``）：执行记录的 skill 身份（请求行 manifest
的 ``skill_commit`` 完整 sha 优先、node_runs ``skill_version`` 的
``ref@commit12`` 前缀）与当前有效绑定的**可证明 commit** 比较，三态：

- **latest 绑定**（节点显式 ``skill: latest``、空 ref 归一、legacy
  兜底）：恒定排除。latest 跟随 live HEAD，upgrade 判定之后 HEAD 仍可
  前进——不做 live rev-parse 的 commit 对比证明不了任何东西，零 git
  I/O 下 latest 绑定不可继承；
- **pinned ref**：与 DB 锁文档（``global_settings.skill_lock``，由调用方
  经 ``SkillLockStore(job_db)`` 直读、绕开 ``SkillManager`` 的 5s
  doc cache）内 ``refs[ref]`` 比较，证明相等（沿用前缀等长截断语义）
  才可继承；
- **锁内无该 ref / 无锁文档**：不可证明 → 排除。**upgrade 永不触发首次
  pin**——pin 写只属于 dispatch 热路径与 ``make skills-lock``。

纪律（#759 P1 复核「事务内重验副作用面」）：本模块不跑 git 子进程、不碰
FileLock、不写锁文档——判定只剩纯 DB 读（``read_skill_lock``）
+ 字符串比较，因此可以在持有 ``implementation-publication`` advisory 锁的
guard 事务内安全重验（``job_workflow_upgrade_apply``），事务回滚不留任何
skill 面副作用。
"""

from __future__ import annotations

import logging

from server.app.agent_catalog import AgentDefinition
from server.app.jobs import JobQueries
from server.app.skills.config import LATEST_REF, SkillsLock
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.workflow_node_skill import effective_node_skill

logger = logging.getLogger(__name__)


def read_skill_lock(job_db: JobQueries) -> SkillsLock | None:
    """skill 锁文档的 DB 直读（照 ``_published_catalog`` 模板）。

    skill 身份判定是安全敏感读（产物冒充检查）：``SkillManager._doc_cache``
    的 5s TTL 会把「``make skills-lock`` 重解析不可见」的 stale 窗口人为
    拉宽（#759 P1）——升级是低频管理操作，这里经 ``SkillLockStore(job_db)``
    直读 ``global_settings.skill_lock``（BOUNDARY-DATA-001 门面），plan 与
    guard 事务内重验走同一权威读取。只读不写：upgrade 永不触发首次 pin
    （pin 写只属于 dispatch 热路径与 ``make skills-lock``）。读取失败返回
    None（保守：全部 skill 绑定节点不可证明 → 重跑）。"""
    from server.app.services.skill_lock_store import SkillLockStore

    try:
        return SkillLockStore(job_db).get_lock()
    except Exception:
        # #204 broad-except audit: 锁文档读取失败（DB 断连、文档损坏等
        # 数据态故障）降级为「skill 面全部不可证明」——保守重跑，不让升级
        # 500。与 _published_catalog 的降级方向一致。
        logger.debug("skill lock document unavailable", exc_info=True)
        return None


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
    前缀。前缀形态与锁内完整 sha 比前 12 位（git 短 sha 语义）。
    """
    skill_commit = record[2]
    if skill_commit:
        return skill_commit
    skill_version = record[3]
    if "@" in skill_version:
        return skill_version.rsplit("@", 1)[1]
    return ""


def _skill_commit_matches(
    skill_lock: SkillsLock | None,
    binding: tuple[str, str],
    record: tuple[str, str, str, str],
) -> bool:
    """执行时 skill commit 与当前有效绑定的可证明 commit 是否一致。

    latest（含空归一与 legacy 兜底）恒 False（恒定排除）；pinned ref 只认
    DB 锁文档的 ``refs[ref]``——锁内无条目即不可证明。upgrade 永不 pin、
    永不跑 git（模块 docstring 的纪律）。
    """
    ref = binding[1] or LATEST_REF
    if ref == LATEST_REF:
        return False
    executed = _executed_skill_commit(record)
    if not executed:
        return False
    locked = skill_lock.skills.get(binding[0]) if skill_lock is not None else None
    current = locked.refs.get(ref) if locked is not None else None
    if not current:
        return False
    return current[: len(executed)] == executed


def skill_excluded_nodes(
    resolved_agents: dict[str, AgentDefinition],
    definition: WorkflowDefinition,
    executed: dict[str, tuple[str, str, str, str]],
    skill_lock: SkillsLock | None,
) -> frozenset[str]:
    """skill 内容身份不可证明/已漂移的 agent 节点集（codex 五轮 P1-A，#759 收紧）。

    只看**有效绑定 skill** 的 agent 节点——无绑定的 agent 节点没有 skill
    面（dispatch 侧即节点失败，由 P1-1 的哈希维度覆盖）。``skill_lock``
    是调用方从 DB 直读的锁文档（plan 与 guard 事务内重验走同一权威读取）；
    None（从未播种或读取失败）时全部绑定节点不可证明 → 排除。
    """
    excluded: set[str] = set()
    for key, node in definition.executable_nodes.items():
        if node.node_type != "agent":
            continue
        binding = _effective_skill_binding(node, resolved_agents.get(key))
        if binding is None:
            continue
        record = executed.get(key)
        if record is None or not _skill_commit_matches(skill_lock, binding, record):
            excluded.add(key)
    return frozenset(excluded)
