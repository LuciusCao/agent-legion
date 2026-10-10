"""skill:latest 继承判定的 HEAD 解析编排（issue #1148，方向 1 定稿）。

#645/#759 起 inherit 升级对 skill:latest 绑定恒定排除——latest 跟随 live
HEAD 永不入锁（#322），判定当时拿不到「当前 HEAD」这一值，commit 对比
无从谈起。代价（#1148）：两次 revision 间只改一个 skill pin，全部 latest
agent 节点（每个 5–11 分钟 LLM 执行）被整体重置重跑。执行侧其实已持久化
每次运行实际物化的 skill 身份（请求行 manifest 的 ``skill_commit`` 完整
sha + node_runs ``skill_version`` 的 ``ref@commit12`` 前缀，
``job_workflow_upgrade_skill`` 的 ``_executed_skill_commit`` 在读）；缺的
只是 plan 时刻的「当前 HEAD commit」。

本模块是 upgrade 链路唯一允许 git I/O 的位置：plan 阶段（guard 事务外、
lease guard 之前）对判定涉及的每个 latest 绑定 skill key 做一次有界
``git rev-parse HEAD``——subprocess 超时 5 秒、整批合计预算 30 秒
（#1166 P2：剩余预算不足单次超时的后续 key 直接 None 保守排除，批级
墙钟有界，管理请求不再先被 HTTP 超时杀掉）；仓库缺失、git 不可用、
超时或输出非 40-hex 都归入 None（保守排除，不 500）。解析出的 HEAD 作为
**数据**传给判定模块（``skill_excluded_nodes(latest_commits=...)``，零
git I/O 纪律不变）；guard 事务内重验沿用 plan 的同一常量（#759 P1：
事务内零 git 子进程、零副作用，重验只剩纯 DB 读 + 字符串比较——含 plan
传入的 HEAD 常量比较）。

残余窗口（#1166 P1 边缘项分诊为收窄，边界如实记录）：HEAD 在
plan→guard→commit 期间前进不触发已继承节点重跑——继承语义是「产物按
当时执行的内容产出且此后未被重跑」，dispatch 下次执行自然消费新 HEAD
（latest 永不入锁，#322）。与 pinned relock 窗口的差异：pinned 在 guard
事务内比对锁文档冻结 commit，plan→guard 间的 relock 被重验抓住（其窗口
只剩 guard 重验后→下次 dispatch）；HEAD 无锁域保护、不入锁文档，guard
事务内没有任何可比对信号（唯一真检测 = 事务内 rev-parse，被 #759 零 git
纪律排除；锁文档 ``resolved_at`` 只随 relock/刷新移动、与 HEAD 前进相互
独立，不可作代理信号），窗口为 plan 起到 commit 止。

低频管理操作：catalog 在此与 ``job_workflow_upgrade_impl`` 各读一次
（fresh 直读、同源 API）——升级非热路径，以重复读换模块独立（impl 不
反向依赖本编排层）。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.services.agent_node_profile import (
    build_capability_index,
    resolve_agent_node_profile,
)
from server.app.skills.config import LATEST_REF
from server.app.skills.skill_roots import default_skill_base_dir
from server.app.workflows.workflow_node_skill import effective_node_skill

if TYPE_CHECKING:
    from server.app.agent_catalog import AgentDefinition
    from server.app.jobs import JobQueries
    from server.app.workflows.definition import WorkflowDefinition

logger = logging.getLogger(__name__)

#: rev-parse 的墙上预算（秒）：阻塞的 git 进程（大仓库 fsck、NFS 挂起）
#: 只能吃满这一窗口，随后按解析失败处理（None → 保守排除）。
_REV_PARSE_TIMEOUT_SECONDS = 5.0

#: 整批 rev-parse 的墙钟总预算（秒，#1166 P2）：单 key 的 5s 只是局部
#: 有界，串行循环 N 个 key 的理论最坏是 N×5s（NFS 挂起形态）——管理
#: 请求会先被 HTTP 超时杀掉、走不到 None 保守降级。批级 deadline 封死
#: 该形态：剩余预算不足一次满超时（``_REV_PARSE_TIMEOUT_SECONDS``）时
#: 不再启动新 rev-parse，该 key 直接按解析失败处理（None → 保守排除）。
_REV_PARSE_BUDGET_SECONDS = 30.0

#: git 输出的合法形态（完整 sha）——其他输出一律按解析失败处理。
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

#: 两段 skill key 的字符集白名单：ASCII 字母/数字起头，其后字母数字/
#: 点/下划线/连字符（skill key 的现实字符集）。白名单外的任何形态
#: （NUL、unicode、空格、``.``/``..`` 段、绝对前缀、非两段）→ 仓库不可
#: 解析 → 保守排除（#1148 评审 P2-1：NUL key 会穿字符串结构校验、在
#: subprocess 参数编码处抛 ValueError → 升级 500，失败语义回归）。白名单
#: 同时封死路径逃逸形态——该字符集构造不出 ``..``/分隔符/绝对路径分量，
#: 无需再做 symlink resolve / containment（评审 P3-3，选择白名单方案）。
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class SkillLatestHeads:
    """plan 阶段的 latest HEAD 快照（guard 重验沿用的常量）。"""

    #: skill key → 当前 HEAD commit（40-hex）；None = 解析失败（保守排除）。
    commits: Mapping[str, str | None]
    #: 有效绑定（节点显式 + legacy 兜底）为 latest 的节点 key 集。
    bound_nodes: frozenset[str]


def resolve_latest_skill_heads(
    job_db: JobQueries,
    job: dict[str, Any],
    definition: WorkflowDefinition,
    *,
    base_dir: Path | None = None,
) -> SkillLatestHeads:
    """plan 阶段（事务外）解析 latest 绑定的当前 HEAD 与绑定节点集。

    catalog 解析失败（DB 断连等数据态故障）→ 空结果（latest 绑定全部
    保守排除，与 #759 恒排除行为同向）；每个 key 的 rev-parse 失败独立
    降级（该 skill 为 None），不影响其他 key。整批解析受
    ``_REV_PARSE_BUDGET_SECONDS`` deadline 约束（#1166）：预算耗尽后
    未启动的 key 直接 None（保守排除），整轮墙钟有界。
    ``base_dir``（测试注入）默认 skill root（``~/.agents/skills``，
    ``skill_roots`` 单一来源）。
    """
    catalog = _published_catalog(job_db, str(job["workspace_id"]))
    if catalog is None:
        return SkillLatestHeads({}, frozenset())
    index = build_capability_index(catalog)
    keys: set[str] = set()
    bound: set[str] = set()
    for key, node in definition.executable_nodes.items():
        if node.node_type != "agent":
            continue
        profile = resolve_agent_node_profile(node, catalog, index=index)
        binding = _effective_binding(node, profile)
        if binding is None or (binding[1] or LATEST_REF) != LATEST_REF:
            continue
        bound.add(key)
        keys.add(binding[0])
    root = Path(base_dir) if base_dir is not None else default_skill_base_dir()
    commits: dict[str, str | None] = {}
    # #1166 P2 批级预算：见 ``_REV_PARSE_BUDGET_SECONDS`` 注释。只在
    # ``deadline - now >= 单次超时`` 时启动 rev-parse——启动过的调用各自
    # 受单次超时约束且不晚于 deadline 结束，整轮墙钟 ≤ 总预算。
    deadline = time.monotonic() + _REV_PARSE_BUDGET_SECONDS
    for skill_key in sorted(keys):
        repo = _skill_repo_dir(root, skill_key)
        if repo is None:
            commits[skill_key] = None
            continue
        if deadline - time.monotonic() < _REV_PARSE_TIMEOUT_SECONDS:
            logger.debug(
                "skill HEAD rev-parse batch budget (%.1fs) exhausted before key %s",
                _REV_PARSE_BUDGET_SECONDS,
                skill_key,
            )
            commits[skill_key] = None
            continue
        commits[skill_key] = _rev_parse_head(repo)
    return SkillLatestHeads(commits, frozenset(bound))


def latest_proven_nodes(
    heads: SkillLatestHeads | None, implementation_excluded: frozenset[str] | set[str]
) -> frozenset[str] | None:
    """S5 消费的 ``latest_proven``（#1148）：内容身份已证明的 latest 绑定节点集。

    权威判定在 P1-A skill 面（``skill_excluded_nodes`` 以同一
    ``latest_commits`` 输入做 executed==HEAD 比较）：latest 绑定节点未被
    执行面排除 ⟹ skill 面已证明其内容一致。``heads`` 为 None（调用方未
    解析）返回 None——S5 对显式 latest 恢复无条件排除（#759 旧行为）。
    被其他执行面（P1-1 哈希 / mutable）排除的 latest 节点不进集合：它们
    已是 S4 种子，S5 的重复排除对闭包结果无影响（保守方向）。
    """
    if heads is None:
        return None
    return heads.bound_nodes - frozenset(implementation_excluded)


def _published_catalog(
    job_db: JobQueries, workspace_id: str
) -> Mapping[str, AgentDefinition] | None:
    """workspace 的 published Agent catalog（fresh 直读；失败 → None 保守降级）。"""
    from server.app.services.agent_node_profile_catalog import fresh_legacy_agent_catalog

    try:
        return fresh_legacy_agent_catalog(job_db, workspace_id)
    except Exception:
        # #204 broad-except audit: catalog 读取失败（DB 断连等数据态故障）
        # 降级为「latest 绑定全部不可证明」——保守重跑，不让升级 500。
        # 与 job_workflow_upgrade_impl._published_catalog 同款降级方向。
        logger.debug("published agent catalog unavailable for skill heads", exc_info=True)
        return None


def _effective_binding(node: Any, profile: Any) -> tuple[str, str] | None:
    """(key, ref) 有效绑定——与 ``job_workflow_upgrade_skill`` 同款优先级。

    ``effective_node_skill`` 在两侧皆空时抛 ValueError（dispatch 侧即节点
    失败）；这里 None 表示无 skill 面。
    """
    try:
        return effective_node_skill(node, profile.skill if profile else "")
    except ValueError:
        return None


def _skill_repo_dir(root: Path, skill_key: str) -> Path | None:
    """skill key → in-place 仓库路径（``<root>/<group>/<name>``）。

    段规则与 ``SkillManager._parse_skill_key`` 一致（相对、两段、无
    ``..``）；loader 与 Agent 定义来源的 key 都已过校验，这里的防御性
    再验只防未校验值进 subprocess 参数（不合式 → None，不跑 git）。
    字符集白名单（``_SEGMENT_RE``）在段规则之上：NUL 等 OS 级非法字符
    会在 subprocess 参数编码处抛 ValueError（POSIX）——白名单先行拒绝
    即不触发（评审 P2-1；``_rev_parse_head`` 的 except 兜底 ValueError
    为双保险）。空段/绝对前缀/``.``/``..``/三段以上均不匹配白名单，
    旧结构校验被完整覆盖。
    """
    parts = skill_key.split("/")
    if len(parts) != 2 or not all(_SEGMENT_RE.fullmatch(part) for part in parts):
        return None
    return root / parts[0] / parts[1]


def _rev_parse_head(repo: Path) -> str | None:
    """repo 当前 HEAD commit（40-hex）；任何失败 → None。

    超时、git 不可用、非仓库、输出异常、参数含 OS 级非法字符（NUL 兜底）
    都归入 None——该 skill 的 latest 绑定保守排除，升级不因 skill 仓库
    状态 500。env 剔除 ``GIT_`` 前缀变量（与 ``SkillManager._run_git``
    同款：防 git hook 环境的 ``GIT_DIR`` 等泄漏进临时仓库解析）。各失败
    分支带 debug 日志（与 ``_published_catalog`` / ``read_skill_lock`` 的
    降级日志纪律对齐，评审 P3-2）。
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD^{commit}"],
            capture_output=True,
            text=True,
            timeout=_REV_PARSE_TIMEOUT_SECONDS,
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        # ValueError = 参数含 NUL 等编码非法字符（_skill_repo_dir 白名单
        # 拒绝后的双保险）；OSError 含 git 不在 PATH；SubprocessError 含
        # TimeoutExpired（超时预算）。
        logger.debug("skill HEAD rev-parse failed for %s", repo, exc_info=True)
        return None
    if result.returncode != 0:
        logger.debug("skill HEAD rev-parse exited %s for %s", result.returncode, repo)
        return None
    commit = result.stdout.strip()
    if not _COMMIT_RE.fullmatch(commit):
        logger.debug("skill HEAD output not a 40-hex commit for %s", repo)
        return None
    return commit
