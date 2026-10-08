"""已删 job 拥有的节点日志路径推导（#958 / PR #1065 删除路径模型）。

从 ``job_deletion_trash`` 拆出（文件预算）：删除只作用于能从该 job 自身记录
精确推导的日志——写入方（``workflow_worker.claim_submit`` / ``shards``）与
本模块共用 ``storage_paths.job_node_log_name``，不列举共享日志目录、不做
前缀 glob。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path

from server.app.settings import Settings
from server.app.storage_paths import job_node_log_name, resolve_data_path

logger = logging.getLogger(__name__)

# 分片日志名的反解析：贪婪 node_key 回溯到最后一个 ``-shard-<数字>.log``，
# 拆分唯一；命中后还须与 job_node_log_name 重新生成的名字逐字相等（拒绝
# 前导零等非本命名函数产出的形态）。DOTALL：node_key 允许任意字符。
_SHARD_LOG_NAME = re.compile(r"(?P<node_key>.+)-shard-(?P<index>[0-9]+)\.log", re.DOTALL)


def deleted_job_log_paths(
    settings: Settings,
    job_id: str,
    node_keys: Iterable[str],
    run_logs: Iterable[tuple[str, str]] = (),
) -> list[Path]:
    """列出已删 job 拥有的节点日志：只删能从本 job 记录精确推导的文件名，
    不列共享日志目录、不做前缀 glob。

    删除路径模型（PR #1065）：job_id 与 node_key 都可含连字符（source_id 只把
    ``/`` 换成 ``_``），``{job_id}-*`` 前缀会命中兄弟 job 的日志。

    - 普通日志：快照节点 key（job_nodes ∪ node_runs）经 ``job_node_log_name``
      精确生成；
    - 分片日志：取自删除事务内（持 job-mutation 锁、删行之前）读取的
      ``node_runs.(node_key, log_path)`` 快照——每次
      claim（本地 ``_lease_claims`` / 远端 ``claim_promote``）都先插入带该
      log_path 的 node_runs 行、之后才有日志写入，node_runs 只随 job 删除，
      rerun / workflow 升级删的是 node_shards 不是 node_runs，故覆盖每个写过
      的分片日志。路径须解析在 ``logs/jobs`` 下，文件名须等于
      ``job_node_log_name(job_id, 该行 node_key[, 数字索引])``；
    - 残留风险只剩写入期即共用同一文件名的命名碰撞（如 job ``A`` 节点
      ``x-y`` 与 job ``A-x`` 节点 ``y`` 都写 ``A-x-y.log``），删除侧无从区分。

    文件系统探测逐路径容错（超长文件名 ENAMETOOLONG 等 OSError 视为不存在并
    告警），从不抛出：行已提交删除，日志清理只能是尽力而为。
    """
    log_dir = settings.logs_dir / "jobs"
    names = {job_node_log_name(job_id, node_key) for node_key in node_keys}
    for node_key, raw_log_path in run_logs:
        name = _owned_run_log_name(settings, job_id, node_key, raw_log_path)
        if name is not None:
            names.add(name)
    paths: list[Path] = []
    for name in sorted(names):
        path = log_dir / name
        try:
            if path.exists():
                paths.append(path)
        except OSError:
            logger.warning("Cannot probe log %s of deleted job %s", path, job_id, exc_info=True)
    return paths


def _owned_run_log_name(
    settings: Settings, job_id: str, node_key: str, raw_log_path: str
) -> str | None:
    """node_runs.log_path 属于本 job 本节点时返回其文件名，否则 None。"""
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
    if name == job_node_log_name(job_id, node_key):
        return name
    prefix = f"{job_id}-"
    match = _SHARD_LOG_NAME.fullmatch(name, len(prefix)) if name.startswith(prefix) else None
    if match is None or match["node_key"] != node_key:
        return None
    if name != job_node_log_name(job_id, node_key, int(match["index"])):
        return None
    return name
