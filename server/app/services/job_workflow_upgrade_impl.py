"""实现身份判定：继承候选的执行时身份 vs 当前 published（#645 codex 四轮 P1-1）。

普通 job 不 pin 版本（EXEC-CODE-002/003）：每次 dispatch 现场解析
workspace 当前 published 的 node_code（code 节点）或 Agent 定义（agent
节点）。节点定义未变而实现重发布时，inherit diff 的定义哈希两侧相等——
旧 job 产物按旧实现产出、升级后同节点重跑执行新实现，继承会把旧实现
的产物冒充新 revision 的产物。

判定（``implementation_excluded_nodes``）：

- **执行时身份**：``node_runs.agent_definition_hash``（schema v85）优先
  ——claim 时刻写入的身份镜像（agent 行 = dispatch 解析到的 Agent 定义
  哈希、code 行 = code 文本 sha256），retention 永不删除 run 行，本地
  code 池执行的身份记录也在此；请求行 fallback 覆盖 v85 前的
  Worker/Agent 历史作业。取该节点**最新一次** completed 执行。
- **当前身份**：与 dispatch 同款解析——code 节点
  ``NodeCodeService.get_effective_code`` 的 ``code_hash``；agent 节点按
  capability 解析唯一 published Agent 的 ``definition_hash()``。
- 两者**证明相等**才可继承；不可证明（两侧记录都缺失、实现未发布、
  capability 漂移、解析异常）或**已漂移**（不等）→ 排除继承，该节点及
  下游闭包重跑。
- **Agent 定义侧 runtime_mutable 键**（codex 四轮复审 HIGH-2）：agent
  节点的有效 config_schema 主体来自 Agent 定义（dispatch 侧
  ``merge_reserved_execution_schema(definition.config_schema, …)``，节点
  自声明被定义覆盖）——Agent 定义不变、只翻转 workspace override 的
  runtime_mutable 值再翻回时，frozen 段与实现身份两侧全等，diff 层的
  节点自声明判定覆盖不到定义侧键。定义 schema 含此类键的 agent 节点
  并入本排除集（恒重跑），解析不到唯一 published 的节点 P1-1 已排除、
  不重复计入。
- **skill 内容身份**（codex 五轮 P1-A）：agent 节点的执行内容还有
  skill 绑定（``effective_node_skill``：节点绑定优先，
  ``AgentDefinition.skill`` 的 legacy 兜底皆空即节点失败）。S5 只排除
  节点显式声明 ``skill: latest`` 的面——legacy 兜底与显式具体 tag 都
  逃过 S5；仓库 HEAD 前进或 ``make skills-lock`` 重解析 tag 后，节点
  定义与 Agent 定义哈希都不变，inherit 保留按旧 skill commit 产出的
  产物而 dispatch 已会执行新 commit。判定在姊妹模块
  ``job_workflow_upgrade_skill``（``skill_excluded_nodes``）：执行记录
  的 skill 身份与当前有效绑定解析出的 commit 比较，证明相等才可继承。

v85 之前本地 code 池执行无身份记录 → 一律「不可证明」恒重跑；v85 起
claim 落列，本地池 code 节点与 Worker/Agent 节点同权可证明。
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.agent_catalog import AgentDefinition
from server.app.jobs import JobQueries
from server.app.services.job_workflow_upgrade_skill import skill_excluded_nodes
from server.app.services.node_config_runtime import runtime_mutable_keys
from server.app.workflows.definition import WorkflowDefinition

logger = logging.getLogger(__name__)

#: 请求行的 kind='code'（与 agent_execution_requests 检查约束一致）。
#: v85 起比较口径按 node_type 选（见 implementation_excluded_nodes），
#: 请求行 kind 只在读取端 fallback 的 SQL 里参与 done 请求筛选。
_CODE_KIND = "code"


def _latest_execution_identities(
    job_db: JobQueries, job_id: str, node_keys: frozenset[str]
) -> dict[str, tuple[str, str, str, str]]:
    """node_key → 该节点最新完成执行的身份记录（BOUNDARY-DATA-001 门面）。

    走 ``JobQueries.latest_done_request_identities``
    （``jobs/queries/upgrade_impl_identity``）：node_runs 身份列优先
    （v85+ 执行 / 本地 code 池），请求行 fallback（历史 Worker/Agent
    作业）；无任何记录（本地池 v85 前执行 / retention 已清扫）→ 该节点
    不在返回值里（调用方按不可证明处理）。
    """
    return job_db.latest_done_request_identities(job_id, node_keys)


def _published_catalog(job_db: JobQueries, workspace_id: str) -> dict[str, AgentDefinition] | None:
    """workspace 的 published Agent catalog（直读，绕过 5s 热路径缓存）。

    P1-1 身份比较是安全敏感读（产物冒充检查）：``published_agent_definitions``
    的 ~5s 缓存会把「重发布不可见」的 stale 窗口人为拉宽（复审 MEDIUM-1
    注记）——升级是低频管理操作，这里直读 store（一次 DB 往返）消除该
    拉宽面。plan 与升级事务之间的 TOCTOU 由 upgrade 事务内的重验收口
    （codex 五轮 P2-C），本函数只去掉缓存这个额外放大器。读取失败返回
    None（保守处理）。"""
    from server.app.services.versioned_entities import EntityType, VersionedEntityStore

    try:
        entity_type: EntityType = "agent"
        entities = VersionedEntityStore(job_db, entity_type).list_published(workspace_id)
        return {
            entity.entity_key: AgentDefinition.model_validate(entity.definition)
            for entity in entities
        }
    except Exception:
        # #204 broad-except audit: catalog 读取失败（DB 断连等数据态故障）
        # 降级为「agent 面全部不可证明」——保守重跑，不让升级 500。
        logger.debug("published agent catalog unavailable", exc_info=True)
        return None


def _resolved_agent_nodes(
    catalog: dict[str, AgentDefinition] | None, definition: WorkflowDefinition
) -> dict[str, AgentDefinition]:
    """node_key → agent 节点解析到的唯一 published Agent 定义。

    与 ``derive_agent_routes`` 同款 capability → 唯一 published 解析；
    0 个或多个 published（数据态漂移）、catalog 不可用都不解析（调用方
    按不可证明处理）。
    """
    if catalog is None:
        return {}
    by_capability: dict[str, list[AgentDefinition]] = {}
    for agent_definition in catalog.values():
        by_capability.setdefault(agent_definition.capability, []).append(agent_definition)
    resolved: dict[str, AgentDefinition] = {}
    for key, node in definition.executable_nodes.items():
        if node.node_type != "agent":
            continue
        candidates = by_capability.get(node.capability, [])
        if len(candidates) == 1:
            resolved[key] = candidates[0]
    return resolved


def _current_agent_identities(
    catalog: dict[str, AgentDefinition] | None, definition: WorkflowDefinition
) -> dict[str, str]:
    """node_key → agent 节点当前 published 实现的定义哈希。"""
    identities: dict[str, str] = {}
    for key, agent_definition in _resolved_agent_nodes(catalog, definition).items():
        try:
            identities[key] = agent_definition.definition_hash()
        except Exception:
            # #204 broad-except audit: 纯内存序列化失败即数据态损坏，
            # 该节点按不可证明处理。
            logger.debug("agent definition hash failed for %s", key, exc_info=True)
    return identities


def _agent_definition_mutable_nodes(
    catalog: dict[str, AgentDefinition] | None, definition: WorkflowDefinition
) -> frozenset[str]:
    """Agent 定义 schema 含 runtime_mutable 键的 agent 节点集（复审 HIGH-2）。"""
    return frozenset(
        key
        for key, agent_definition in _resolved_agent_nodes(catalog, definition).items()
        if runtime_mutable_keys(agent_definition.config_schema)
    )


def _current_code_identities(
    job_db: JobQueries,
    custom_nodes_enabled: bool,
    workspace_id: str,
    definition: WorkflowDefinition,
) -> dict[str, str]:
    """node_key → code 节点当前 published 实现的 code_hash。

    特性关闭（``workflows.custom_nodes_enabled``）时 dispatch 无 code 可
    解析——全部按不可证明处理（空 dict）。单个节点解析失败/无 published
    同样不进 dict。
    """
    if not custom_nodes_enabled:
        return {}
    from server.app.services.node_codes import NodeCodeService

    try:
        service = NodeCodeService(job_db, custom_nodes_enabled=True)
    except Exception:
        # #204 broad-except audit: 构造失败（数据态）→ 全部保守重跑。
        logger.debug("node code service unavailable", exc_info=True)
        return {}
    identities: dict[str, str] = {}
    for key, node in definition.executable_nodes.items():
        if node.node_type == "agent":
            continue
        try:
            row = service.get_effective_code(workspace_id, definition.key, key)
        except Exception:
            # #204 broad-except audit: 读路径数据态故障 → 该节点保守重跑。
            logger.debug("published node code unavailable for %s", key, exc_info=True)
            continue
        if row is not None:
            identities[key] = str(row["code_hash"])
    return identities


def implementation_excluded_nodes(
    job_db: JobQueries,
    job: dict[str, Any],
    definition: WorkflowDefinition,
    *,
    custom_nodes_enabled: bool = True,
    skill_manager: Any = None,
) -> frozenset[str]:
    """执行面排除集：实现身份不可证明/已漂移 + Agent 定义 runtime_mutable 键。

    P1-1 只判定 ``definition`` 的可执行节点。判定失败的任何分支都归入
    「不可证明」→ 排除（保守方向：多跑不串数据）。复审 HIGH-2 的 Agent
    定义侧 runtime_mutable 键（定义不变、只翻转 override 值再翻回时
    frozen/身份两侧全等）并入同一排除集——对 ``compute_inherit_reset_
    nodes`` 而言都是「无论 diff 是否变化都强制重跑」的节点。
    """
    executable = frozenset(definition.executable_nodes)
    if not executable:
        return frozenset()
    workspace_id = str(job["workspace_id"])
    job_id = str(job["id"])
    executed = _latest_execution_identities(job_db, job_id, executable)
    catalog = _published_catalog(job_db, workspace_id)
    agent_current = _current_agent_identities(catalog, definition)
    code_current = _current_code_identities(job_db, custom_nodes_enabled, workspace_id, definition)
    excluded: set[str] = set(_agent_definition_mutable_nodes(catalog, definition))
    # codex 五轮 P1-A：skill 内容身份（姊妹模块）——skill_manager 是与
    # dispatch 同源的 SkillManager（latest=live HEAD、tag=DB 锁）。
    excluded |= skill_excluded_nodes(
        _resolved_agent_nodes(catalog, definition), definition, executed, skill_manager
    )
    for key, node in definition.executable_nodes.items():
        record = executed.get(key)
        if record is None:
            # 无执行记录（本地池执行 / retention 清扫 / 从未产出）：
            # 旧产物按哪份实现产出不可知 → 恒重跑。
            excluded.add(key)
            continue
        executed_hash = record[1]
        if not executed_hash:
            excluded.add(key)
            continue
        # 比较口径按 node_type 选（#645 v85）：node_runs 段无 kind 列，
        # agent 节点对 Agent 定义哈希、code 节点对 code_hash。请求行
        # fallback 的 kind 错乱防御（agent 节点挂 code 请求行）由哈希
        # 值域天然覆盖：Agent definition_hash（定义 JSON 序列化摘要）与
        # code sha256 相等概率可忽略，错乱即不等即保守排除。node_runs
        # 段的 kind 是空串哨兵（非 'code'）——口径选择必须只看 node_type，
        # 不能让哨兵值把 code 节点误路由到 agent catalog。
        current = agent_current.get(key) if node.node_type == "agent" else code_current.get(key)
        if current is None or current != executed_hash:
            # 当前无 published 身份或与执行时身份不等 → 漂移/不可证明。
            excluded.add(key)
    return frozenset(excluded)
