"""Job 删除的事务外本地清理与 ``.trash`` TTL 回收（#958）。

DB 是删除的唯一权威：``JobDeletionService`` 先在
``lease_guarded_mutation`` 内删除 jobs 行并提交，提交成功后才由本模块在事务
外处理本地 job_dir 与节点日志——它们只是执行暂存与可淘汰缓存，产物权威副本在
对象存储（EXEC-ARTIFACT-STORE-001），所以本地清理失败不影响删除结果。

选「先提交后移动」而非「先移动后提交、失败反向补偿」：后者移动时 job 尚未
被 job-mutation 锁住，派发可能往刚被移走的目录写入、使补偿撞上重建目录；进程
崩溃于移动与提交之间更会留下「行仍在、缓存却躺在 trash」的活 job，TTL 回收
随后吞掉它的本地缓存。前者任何失败只会多留一份无行引用的缓存，DB 与对象存储
始终一致。各失败点的终态：

- 事务失败（冲突 / 外键 / DB 错误）：行仍在，文件系统零改动；
- 提交成功、移入 trash 失败，或进程崩溃于提交与移入之间：删除仍判成功，
  残留留在原路径（无自动回收；job id 由 workspace/workflow/source 确定性派生，
  同源重建的 job 会复用该路径，见 docs/data-layout.md）；
- 移入 trash 成功、删除失败或进程崩溃：残留在 ``.trash/<operation_id>/``，
  由 ``job_deletion_trash_sweep.sweep_deletion_trash`` 按 TTL 回收（只回收带已提交标记的条目）；
- 提交后、移入前同源 job 被重建（确定性 id，create_job 不取 job-mutation
  锁）：移入在 ``job-mutation:<id>`` 锁下复核 jobs 行仍不存在才执行，已重建就
  整体跳过（目录与日志归新 job，交给 retention），绝不移走新 job 的活目录；
  并把「已重建」返回调用方，由其跳过按 job id 的后续清理与删除广播；
- 锁事务在给出复核结果前失败（瞬时 DB 错误）：无法排除重建，同样整体跳过
  并按「已重建」返回，旧 job 的 refs / 对象泄漏交给 orphan GC 与
  ``scripts/gc-s3-jobs.py`` 回收。

日志路径的枚举（含一次共享日志目录列举）在锁外完成，锁内只复核行与
rename——锁事务保持短，不阻塞重建后新 job 的 claim。日志只删能由
``job_node_log_name`` 精确生成的名字，见 ``deleted_job_log_paths``。

锁下只做同文件系统原子 ``os.rename`` 进 trash（跨文件系统 EXDEV 即放弃、留
残留，绝不在锁下拷贝），rmtree 在锁外：原路径瞬间腾空，同 id 重建的 job 不会
看到删了一半的旧目录；半途失败的残留集中在可清扫的位置。``.trash`` 不
提供恢复：提交后 jobs 行已不存在，残留没有可恢复到的归属。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.settings import Settings
from server.app.storage_paths import ManagedPathError, job_node_log_name, resolve_job_dir

if TYPE_CHECKING:
    from server.app.jobs import JobQueries

logger = logging.getLogger(__name__)

TRASH_DIRNAME = ".trash"
# 已提交删除的证明：本模块在提交后、移入任何文件前先写入 operation 目录。
# TTL 回收只处理带此标记的条目——0.7.17 前 _restore_paths 在删除事务回滚、
# 原目录被重建时留下的恢复副本没有标记，其 jobs 行仍在、对无 job_artifacts
# 行的 legacy job 可能是唯一产物副本，一律不自动删除（只记日志提示人工处理）。
COMMITTED_MARKER = ".committed-deletion"
# 提交后 trash 里只有已删 job 的残留，TTL 仅是给仍在 rmtree 的删除操作留的
# 宽限窗口（避免清扫与之抢删）；按 operation 目录 mtime（移入时刷新）计龄。
DELETION_TRASH_TTL = timedelta(hours=24)


def jobs_trash_root(settings: Settings) -> Path:
    return settings.jobs_dir / TRASH_DIRNAME


def logs_trash_root(settings: Settings) -> Path:
    return settings.logs_dir / "jobs" / TRASH_DIRNAME


# 分片日志名的反解析：贪婪 node_key 回溯到最后一个 ``-shard-<数字>.log``，
# 拆分唯一；命中后还须与 job_node_log_name 重新生成的名字逐字相等（拒绝
# 前导零等非本命名函数产出的形态）。DOTALL：node_key 允许任意字符。
_SHARD_LOG_NAME = re.compile(r"(?P<node_key>.+)-shard-(?P<index>[0-9]+)\.log", re.DOTALL)


def deleted_job_log_paths(settings: Settings, job_id: str, node_keys: Iterable[str]) -> list[Path]:
    """列出已删 job 拥有的节点日志：只认 ``job_node_log_name(job_id, k[, i])``
    能生成的文件名，``k`` 取自删除前的节点 key 快照。

    删除路径模型（PR #1065）：job_id 与 node_key 都可含连字符（source_id 只把
    ``/`` 换成 ``_``），``{job_id}-*`` 前缀 glob 会命中 source_id 为
    ``<source>-...`` 的兄弟 job 的日志，绝不使用。

    - 普通日志：按快照 key 精确生成路径，不列目录；
    - 分片日志：shard 索引无法从行记录精确推导（rerun / workflow 升级会删掉
      node_shards 行再按新代次重建，旧代次更大索引的日志仍属本 job），故锁外
      列一次目录，对每个名字做「锚定反解析 + 用命名函数重新生成后全等」——
      兄弟 job ``<source>-<k>-shard-0`` 的 ``…-shard-0-<node>.log`` 不满足；
    - 残留风险只剩写入期即已共用同一文件名的命名碰撞（如 job ``A`` 节点
      ``x-y`` 与 job ``A-x`` 节点 ``y`` 都写 ``A-x-y.log``），删除侧无从区分。
    """
    log_dir = settings.logs_dir / "jobs"
    keys = set(node_keys)
    paths = {log_dir / job_node_log_name(job_id, node_key) for node_key in keys}
    prefix = f"{job_id}-"
    try:
        with os.scandir(log_dir) as entries:
            names = [entry.name for entry in entries if entry.name.startswith(prefix)]
    except OSError:
        # 列目录失败只少删分片日志（留原位残留），不让已提交的删除变成 API 错误。
        logger.warning("Cannot list %s for deleted job %s", log_dir, job_id, exc_info=True)
        names = []
    for name in names:
        match = _SHARD_LOG_NAME.fullmatch(name, len(prefix))
        if match is None or match["node_key"] not in keys:
            continue
        if name == job_node_log_name(job_id, match["node_key"], int(match["index"])):
            paths.add(log_dir / name)
    return sorted(path for path in paths if path.exists())


def purge_deleted_job_files(
    job_db: JobQueries,
    job: Mapping[str, Any],
    node_keys: Iterable[str],
    settings: Settings,
    operation_id: str,
) -> bool:
    """提交后把已删 job 的本地目录与日志移入 trash 再删除；失败只记日志。

    返回 True 表示锁下复核发现同源 job 已重建，或复核未能产出结果（锁事务在
    给出存在性前就因 DB 错误失败）：调用方必须跳过按 job id 的后续清理
    （artifact refs、对象存储）与删除广播，否则会误删 / 误报新 job。复核失败
    时无法排除重建，保守跳过的代价只是旧 job 的 refs / 对象泄漏，交给 orphan
    GC（``artifact_orphan_gc``）与 ``scripts/gc-s3-jobs.py`` 兜底。

    跨事务动作前重新校验目标身份与状态：job_dir 按快照行重新解析（重走
    managed-root 包含校验）；移入在 ``job-mutation:<id>`` 短事务锁下复核 jobs
    行仍不存在才执行（同源重建的 job 已落行即整体跳过）。``node_keys`` 是删除
    前快照的节点 key（job_nodes ∪ node_runs），日志按它精确匹配。
    """
    job_id = str(job["id"])
    staged: list[Path] = []
    try:
        storage_dir: Path | None = resolve_job_dir(job, settings.jobs_dir)
    except ManagedPathError:
        logger.warning("Skip local cleanup of deleted job %s: storage_dir escapes", job_id)
        storage_dir = None
    # 日志枚举放锁外：锁内只剩行复核与原子 rename（存在性在锁内再探一次）。
    log_paths = deleted_job_log_paths(settings, job_id, node_keys)
    # None = 锁事务尚未给出存在性结果；yield 之后（如 commit）才失败时它已绑定
    # 真实复核值，照常返回。
    recreated: bool | None = None
    try:
        with job_db.job_mutation_lock(job_id) as recreated:
            if recreated:
                logger.info("Skip local cleanup of deleted job %s: recreated", job_id)
            else:
                if storage_dir is not None and storage_dir.is_dir():
                    staged += _stage(storage_dir, jobs_trash_root(settings) / operation_id, job_id)
                for log_path in log_paths:
                    if log_path.exists():
                        staged += _stage(log_path, logs_trash_root(settings) / operation_id, job_id)
    except Exception:
        # #204 broad-except audit: the jobs row is already committed as
        # deleted, so the lock transaction is a best-effort recheck — its only
        # failure kinds are DB errors (connection loss, lock/commit failure;
        # _stage swallows its own OSError). Failing here must not turn a
        # succeeded deletion into an API error: whatever was renamed before
        # the failure sits in .trash and is purged below, the rest stays as
        # residue at the original path. If the failure hit before the lock
        # yielded its existence check, recreated is still None — "could not
        # verify" must not read as "confirmed not recreated", so the return
        # below reports True and the caller skips the id-scoped refs/object
        # cleanup and the deletion broadcast (cost: a leak the orphan GC and
        # scripts/gc-s3-jobs.py reclaim). logger.exception keeps the traceback.
        logger.exception("Post-commit cleanup lock failed for deleted job %s", job_id)
    for path in staged:
        _remove(path, job_id)
    _prune_empty(jobs_trash_root(settings) / operation_id)
    _prune_empty(logs_trash_root(settings) / operation_id)
    return True if recreated is None else recreated


def _stage(path: Path, trash_dir: Path, job_id: str) -> list[Path]:
    staged = trash_dir / path.name
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)
        # 先落已提交标记再移入：凡含本模块残留的 operation 目录必带标记。
        (trash_dir / COMMITTED_MARKER).write_text(f"{job_id}\n", encoding="utf-8")
        # 锁下只做原子 rename；跨文件系统（EXDEV）不退化为拷贝，留原位残留。
        os.rename(path, staged)
    except OSError:
        # 行已提交删除：移不走只留原位残留（见模块 docstring），不让成功的
        # 删除变成 API 错误。os.rename / mkdir 只抛 OSError。
        logger.exception("Failed to stage %s of deleted job %s into trash", path, job_id)
        return []
    return [staged]


def _remove(path: Path, job_id: str) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        # 残留留在 .trash/<operation_id>，由 sweep_deletion_trash 按 TTL 回收。
        logger.exception("Failed to purge staged %s of deleted job %s", path, job_id)


def _prune_empty(path: Path) -> None:
    """operation 目录只剩已提交标记（或已空）时连同标记一起删掉。"""
    try:
        if path.exists() and all(child.name == COMMITTED_MARKER for child in path.iterdir()):
            (path / COMMITTED_MARKER).unlink(missing_ok=True)
            path.rmdir()
            parent = path.parent
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
    except OSError:
        pass
