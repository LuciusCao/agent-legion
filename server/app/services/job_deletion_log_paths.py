"""已删 job 拥有的节点日志路径推导（#958 / PR #1065 删除路径模型，#1113 分目录）。

从 ``job_deletion_trash`` 拆出（文件预算）：删除只作用于能从该 job 自身记录
精确推导的日志，不列举共享日志目录、不做前缀 glob。

- 新命名（#1113）：写入方（``workflow_worker.claim_submit`` / ``shards``）经
  ``storage_paths.job_node_log_name`` 把节点日志写进 job 独占目录
  ``logs/jobs/by-job/<job_id>/``，删除按 ``job_node_log_dir_name`` 整目录移走；
- 旧扁平名（#1113 前写下的存量）：只认删除事务内快照的 ``node_runs.log_path``
  中、文件名恰等于 ``legacy_job_node_log_name(job_id, 该行 node_key[, 索引])``
  的那些。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path

from server.app.settings import Settings
from server.app.storage_paths import (
    job_node_log_dir_name,
    legacy_job_node_log_name,
    resolve_data_path,
)

logger = logging.getLogger(__name__)

# 旧分片日志名的反解析：贪婪 node_key 回溯到最后一个 ``-shard-<数字>.log``，
# 拆分唯一；命中后还须与 legacy_job_node_log_name 重新生成的名字逐字相等
# （拒绝前导零等非命名函数产出的形态）。DOTALL：node_key 允许任意字符。
_SHARD_LOG_NAME = re.compile(r"(?P<node_key>.+)-shard-(?P<index>[0-9]+)\.log", re.DOTALL)


def deleted_job_log_paths(
    settings: Settings,
    job_id: str,
    run_logs: Iterable[tuple[str, str]] = (),
) -> list[Path]:
    """列出已删 job 拥有的节点日志：目录项（新命名）+ 旧扁平名文件。

    - 新命名：``logs/jobs/by-job/<job_id>/`` 只装本 job 的日志（job_id 独占
      目录，不同 job_id 永不同目录），存在即整目录返回——覆盖每个普通/分片
      日志与已退出图的历史节点，也不留空目录；
    - 旧扁平名：job_id 与 node_key 都可含连字符（source_id 只把 ``/`` 换成
      ``_``），``{job_id}-*`` 前缀与「按节点 key 生成名字」都会命中兄弟 job 的
      日志（job ``A`` 节点 ``x-y`` 与 job ``A-x`` 节点 ``y`` 同为
      ``A-x-y.log``）。故只删本 job 的 node_runs 行**实际登记过**的路径：每次
      claim（本地 ``_lease_claims`` / 远端 ``claim_promote`` / 配置失败）都先
      插入带该 log_path 的 node_runs 行、之后才有日志写入，node_runs 只随 job
      删除，故覆盖本 job 写过的每个旧日志。路径须解析在 ``logs/jobs`` 直属层，
      文件名须等于 ``legacy_job_node_log_name(job_id, 该行 node_key[, 数字索引])``；
    - 残留风险只剩升级前两个 job 都真实写过同一旧文件名（写入期即互相覆盖），
      删除侧无从区分；#1113 起不再产生新的旧扁平名，该集合只减不增。

    文件系统探测逐路径容错（超长文件名 ENAMETOOLONG 等 OSError 视为不存在并
    告警），从不抛出：行已提交删除，日志清理只能是尽力而为。
    """
    log_dir = settings.logs_dir / "jobs"
    candidates: list[Path] = []
    try:
        candidates.append(log_dir / job_node_log_dir_name(job_id))
    except ValueError:
        # 不变式外的 job_id（含 ``/`` 等）从未有过分目录日志，无目录可删。
        logger.warning("Deleted job %r has no per-job log dir name", job_id)
    names = set()
    for node_key, raw_log_path in run_logs:
        name = _owned_legacy_run_log_name(settings, job_id, node_key, raw_log_path)
        if name is not None:
            names.add(name)
    candidates.extend(log_dir / name for name in sorted(names))
    paths: list[Path] = []
    for path in candidates:
        try:
            if path.exists():
                paths.append(path)
        except OSError:
            logger.warning("Cannot probe log %s of deleted job %s", path, job_id, exc_info=True)
    return paths


def _owned_legacy_run_log_name(
    settings: Settings, job_id: str, node_key: str, raw_log_path: str
) -> str | None:
    """node_runs.log_path 是本 job 本节点的旧扁平名日志时返回其文件名，否则 None。"""
    if not raw_log_path:
        return None
    try:
        path = resolve_data_path(raw_log_path, settings.data_dir, allow_missing=True)
        if path.parent != (settings.logs_dir / "jobs").resolve():
            return None
    except (OSError, ValueError, RuntimeError):
        # ManagedPathError 是 ValueError：越出 data 根 / 不可解析的历史路径不删；
        # Path.resolve 遇符号链接环抛 RuntimeError，同样按不可解析处理，不让单条
        # 坏掉的历史 log_path 中断提交后的清理（与仓库其它路径清理一致）。
        return None
    name = path.name
    if name == legacy_job_node_log_name(job_id, node_key):
        return name
    prefix = f"{job_id}-"
    match = _SHARD_LOG_NAME.fullmatch(name, len(prefix)) if name.startswith(prefix) else None
    if match is None or match["node_key"] != node_key:
        return None
    if name != legacy_job_node_log_name(job_id, node_key, int(match["index"])):
        return None
    return name
