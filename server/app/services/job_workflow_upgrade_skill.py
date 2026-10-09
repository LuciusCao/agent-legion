"""skill 内容身份判定：执行时 commit vs 当前可证明 commit（#645 codex 五轮 P1-A；#759 P1 收紧；#1148 latest 精确比对）。

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
  兜底）：与 plan 阶段传入的 HEAD 常量精确比对（#1148）。plan 在 guard
  事务外解析一次 ``git rev-parse HEAD`` 作为**数据**传入
  （``latest_commits``，编排见 ``job_workflow_upgrade_skill_heads``）；
  执行记录 commit（前缀形态按前缀长度比较，与 pinned 分支同语义）等于
  HEAD → 可继承，HEAD 缺失（解析失败/未传入）或执行记录无 commit 或
  不等 → 保守排除。判定后 HEAD 仍可能前进——继承语义是「产物按当时
  执行的内容产出且此后未被重跑」，该残余窗口与 pinned ref 的 guard
  重验后 relock 窗口同构（详见编排模块 docstring）；
- **pinned ref**：与 DB 锁文档（``global_settings.skill_lock``，由调用方
  经 ``SkillLockStore(job_db)`` 直读、绕开 ``SkillManager`` 的 5s
  doc cache）内 ``refs[ref]`` 比较，证明相等（沿用前缀等长截断语义）
  才可继承；
- **锁内无该 ref / 无锁文档**：不可证明 → 排除。**upgrade 永不触发首次
  pin**——pin 写只属于 dispatch 热路径与 ``make skills-lock``。

纪律（#759 P1 复核「事务内重验副作用面」）：本模块不跑 git 子进程、不碰
FileLock、不写锁文档——判定只剩纯 DB 读（``read_skill_lock``）
+ 字符串比较（含 plan 传入的 HEAD 常量比较，#1148），因此可以在持有
``implementation-publication`` advisory 锁的
guard 事务内安全重验（``job_workflow_upgrade_apply``），事务回滚不留任何
skill 面副作用。git rev-parse 只发生在 plan 层
（``job_workflow_upgrade_skill_heads``，事务外有界调用）。

#759 P2-B：锁文档写（dispatch 首次 pin 与 ``make skills-lock`` 重锁，
均经 ``SkillLockStore.put_lock``）与 upgrade 的读共享 ``skill-lock``
全域 advisory 锁——plan 阶段短事务取锁+读，guard 事务内先取锁再无锁
读（``domain_held``），重验到提交之间 relock 被挡住；dispatch 热路径
的解析读不进本域。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from server.app.jobs import JobQueries
from server.app.services.agent_node_profile import AgentNodeProfile
from server.app.skills.config import LATEST_REF, SkillsLock
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.workflow_node_skill import effective_node_skill

logger = logging.getLogger(__name__)


def read_skill_lock(job_db: JobQueries, *, domain_held: bool = False) -> SkillsLock | None:
    """skill 锁文档的 DB 直读（照 ``_published_catalog`` 模板）。

    skill 身份判定是安全敏感读（产物冒充检查）：``SkillManager._doc_cache``
    的 5s TTL 会把「``make skills-lock`` 重解析不可见」的 stale 窗口人为
    拉宽（#759 P1）——升级是低频管理操作，这里经 ``SkillLockStore(job_db)``
    直读 ``global_settings.skill_lock``（BOUNDARY-DATA-001 门面），plan 与
    guard 事务内重验走同一权威读取。只读不写：upgrade 永不触发首次 pin
    （pin 写只属于 dispatch 热路径与 ``make skills-lock``）。读取失败返回
    None（保守：全部 skill 绑定节点不可证明 → 重跑）。

    #759 P2-B（skill-lock 全域 advisory 锁）：``domain_held=False``
    （plan 阶段）经 ``get_lock_locked`` 短事务取锁+读——读到的锁文档
    不旧于任何已完成的 relock；``domain_held=True``（guard 事务内重验）
    由调用方先在 guard 连接上取锁（xact 锁不可跨连接重入，另起短事务
    会与 guard 事务自锁），本读走无锁 ``get_lock``，重验到提交之间
    relock 被挡住。"""
    from server.app.services.skill_lock_store import SkillLockStore

    try:
        store = SkillLockStore(job_db)
        return store.get_lock() if domain_held else store.get_lock_locked()
    except Exception:
        # #204 broad-except audit: 锁文档读取失败（DB 断连、文档损坏等
        # 数据态故障）降级为「skill 面全部不可证明」——保守重跑，不让升级
        # 500。与 _published_catalog 的降级方向一致。
        logger.debug("skill lock document unavailable", exc_info=True)
        return None


def _effective_skill_binding(
    node: WorkflowNode, profile: AgentNodeProfile | None
) -> tuple[str, str] | None:
    """agent 节点的有效 skill 绑定 ``(key, ref)``（dispatch 同款优先级）。

    ``effective_node_skill`` 在两侧皆空时抛 ValueError（dispatch 侧即节点
    失败）——这里返回 None 表示无 skill 面（由 P1-1 的哈希维度覆盖）。"""
    try:
        return effective_node_skill(node, profile.skill if profile else "")
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
    latest_commit: str | None = None,
) -> bool:
    """执行时 skill commit 与当前有效绑定的可证明 commit 是否一致。

    latest（含空归一与 legacy 兜底）与 plan 传入的 HEAD 常量比较
    （``latest_commit``，#1148；None = 未解析/未传入 → 保守排除，与
    #759 恒排除同向），前缀形态按前缀长度截断（与 pinned 分支同语义）；
    pinned ref 只认 DB 锁文档的 ``refs[ref]``——锁内无条目即不可证明。
    upgrade 永不 pin、判定模块零 git I/O（HEAD 由 plan 层解析后传入，
    模块 docstring 的纪律）。
    """
    ref = binding[1] or LATEST_REF
    executed = _executed_skill_commit(record)
    if ref == LATEST_REF:
        if not latest_commit or not executed:
            return False
        return latest_commit[: len(executed)] == executed
    if not executed:
        return False
    locked = skill_lock.skills.get(binding[0]) if skill_lock is not None else None
    current = locked.refs.get(ref) if locked is not None else None
    if not current:
        return False
    return current[: len(executed)] == executed


def skill_excluded_nodes(
    resolved_agents: dict[str, AgentNodeProfile],
    definition: WorkflowDefinition,
    executed: dict[str, tuple[str, str, str, str]],
    skill_lock: SkillsLock | None,
    latest_commits: Mapping[str, str | None] | None = None,
) -> frozenset[str]:
    """skill 内容身份不可证明/已漂移的 agent 节点集（codex 五轮 P1-A，#759 收紧，#1148）。

    只看**有效绑定 skill** 的 agent 节点——无绑定的 agent 节点没有 skill
    面（dispatch 侧即节点失败，由 P1-1 的哈希维度覆盖）。``skill_lock``
    是调用方从 DB 直读的锁文档（plan 与 guard 事务内重验走同一权威读取）；
    None（从未播种或读取失败）时全部绑定节点不可证明 → 排除。
    ``latest_commits``（#1148）是 plan 阶段解析的 latest 绑定 skill key
    → 当前 HEAD commit（``job_workflow_upgrade_skill_heads``，guard 重验
    沿用同一常量）；None（未传入）或条目缺失时该绑定保守排除（与 #759
    恒排除同向）。
    """
    excluded: set[str] = set()
    heads = latest_commits or {}
    for key, node in definition.executable_nodes.items():
        if node.node_type != "agent":
            continue
        binding = _effective_skill_binding(node, resolved_agents.get(key))
        if binding is None:
            continue
        record = executed.get(key)
        if record is None or not _skill_commit_matches(
            skill_lock, binding, record, heads.get(binding[0])
        ):
            excluded.add(key)
    return frozenset(excluded)
