"""S3 ``jobs/`` 与 ``jobs-staging/`` 前缀的孤儿对象扫描与回收（#340）。

job 删除路径（``job_deletion`` → ``job_artifacts.delete_objects``）只按
``job_artifacts.storage_key`` 行驱动删除，失败不重试；``promote_all`` 在
行写入失败时会留下已拷贝的 authority 对象；Worker 直传 staging 后结果
报告丢失则 staging 对象永久滞留。这些对象对应用不可见，只能按前缀列举
对照回收：

- ``jobs/{ws}/{job_id}/{name}``：key 不在 ``job_artifacts.storage_key``
  集合中且 LastModified 超过宽限窗 → 孤儿；
- ``jobs-staging/{ws}/{job_id}/{exec_id}/{name}``：超宽限窗即回收候选
  （``discard_staging`` 是 best-effort，文档明言 lifecycle 是 backstop；
  docs/materials-storage-deployment.md 规划的 1 天短保留）。

设计约束：
- 判定核心（``scan_orphans``）只依赖注入的列举器与 key 存在性函数，
  单测不碰 boto3 / DB；
- ``job_artifacts.storage_key`` 无索引：按 key 反查会退化为每页一次
  全表扫描，故存在性判定单遍全量载入构建内存 set（运维 CLI 场景的
  有意取舍）；
- 删除分批（每批 ``DELETE_BATCH`` 个），幂等可重跑。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from server.app.jobs.queries import JobQueries

logger = logging.getLogger(__name__)

JOBS_PREFIX = "jobs/"
STAGING_PREFIX = "jobs-staging/"

DEFAULT_GRACE_HOURS = 24.0
DEFAULT_STAGING_GRACE_HOURS = 24.0
KEY_PAGE_SIZE = 1000
DELETE_BATCH = 1000


@dataclass(frozen=True)
class ObjectEntry:
    """列举器产出的最小对象事实（boto3 ListObjectsV2 Contents 的投影）。"""

    key: str
    last_modified: datetime
    size_bytes: int


@dataclass
class OrphanReport:
    """dry-run 与 --apply 共用的扫描结果。"""

    authority_orphans: list[ObjectEntry]
    staging_orphans: list[ObjectEntry]

    @property
    def count(self) -> int:
        return len(self.authority_orphans) + len(self.staging_orphans)

    @property
    def total_bytes(self) -> int:
        return sum(o.size_bytes for o in self.authority_orphans) + sum(
            o.size_bytes for o in self.staging_orphans
        )


# 注入面：列举器产出按 key 升序的对象流（S3 ListObjectsV2 本身有序）。
Lister = Callable[[str], Iterator[ObjectEntry]]
# 注入面：DB 侧批量 key 存在性判定（返回入参中存在于 job_artifacts 的子集）。
KeyExistence = Callable[[list[str]], set[str]]


def _past_grace(entry: ObjectEntry, cutoff: datetime) -> bool:
    modified = entry.last_modified
    if modified.tzinfo is None:
        modified = modified.replace(tzinfo=UTC)
    return modified < cutoff


def scan_orphans(
    lister: Lister,
    key_exists: KeyExistence,
    *,
    grace_hours: float = DEFAULT_GRACE_HOURS,
    staging_grace_hours: float = DEFAULT_STAGING_GRACE_HOURS,
    now: datetime | None = None,
) -> OrphanReport:
    """全前缀扫描并返回孤儿对象（纯判定，不删除）。

    两遍列举的原因是判定语义不同：staging 只看宽限窗，authority 还要
    DB 对照。两个前缀在 ListObjectsV2 上本就互不重叠（``jobs/`` 与
    ``jobs-staging/`` 第 6 个字符一个 ``/`` 一个 ``-``）；authority 侧
    按页攒批只是有界化每次传给 ``key_exists`` 的 key 列表。
    """
    now = now or datetime.now(UTC)
    report = OrphanReport([], [])
    authority_cutoff = now - timedelta(hours=grace_hours)

    pending: list[ObjectEntry] = []
    for entry in lister(STAGING_PREFIX):
        if _past_grace(entry, now - timedelta(hours=staging_grace_hours)):
            report.staging_orphans.append(entry)
    for entry in lister(JOBS_PREFIX):
        pending.append(entry)
        if len(pending) >= KEY_PAGE_SIZE:
            report.authority_orphans.extend(
                _authority_orphans_in(pending, key_exists, authority_cutoff)
            )
            pending = []
    if pending:
        report.authority_orphans.extend(
            _authority_orphans_in(pending, key_exists, authority_cutoff)
        )
    return report


def _authority_orphans_in(
    entries: list[ObjectEntry], key_exists: KeyExistence, cutoff: datetime
) -> list[ObjectEntry]:
    """DB 无行且超宽限窗：窗内的未知 key 保留（上传在途/行未写场景）。"""
    known = key_exists([e.key for e in entries])
    return [e for e in entries if e.key not in known and _past_grace(e, cutoff)]


def make_db_key_existence(queries: JobQueries) -> KeyExistence:
    """DB 侧存在性判定：首次调用时单遍全量载入 storage_key 集合
    （BOUNDARY-DATA-001：service 层不落 SQL 字面量）。该列无索引，
    按 key 反查会退化为每页一次全表扫描；单遍顺序扫描 + 内存 set 是
    运维 CLI 的有意取舍（百万 key 量级约百 MB）。"""

    all_keys: set[str] | None = None

    def exists(keys: list[str]) -> set[str]:
        nonlocal all_keys
        if all_keys is None:
            all_keys = queries.all_artifact_storage_keys()
        return {key for key in keys if key in all_keys}

    return exists


# 注入面：删除客户端只暴露 boto3 delete_objects 批删接口的形状。
class BatchDeleter(Protocol):
    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]: ...


def delete_entries(
    client: BatchDeleter,
    bucket: str,
    entries: Iterable[ObjectEntry],
) -> int:
    """分批删除；返回成功删除的对象数（boto3 delete_objects 批接口）。"""
    deleted = 0
    batch: list[ObjectEntry] = []
    for entry in entries:
        batch.append(entry)
        if len(batch) >= DELETE_BATCH:
            deleted += _delete_batch(client, bucket, batch)
            batch = []
    if batch:
        deleted += _delete_batch(client, bucket, batch)
    return deleted


def _delete_batch(client: BatchDeleter, bucket: str, batch: list[ObjectEntry]) -> int:
    response = client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": e.key} for e in batch], "Quiet": True},
    )
    errors = response.get("Errors", [])
    if errors:
        logger.warning(
            "s3 jobs GC: %d/%d 对象删除失败（下一轮扫描会重试）",
            len(errors),
            len(batch),
        )
    return len(batch) - len(errors)


def apply_gc(
    client: BatchDeleter,
    bucket: str,
    report: OrphanReport,
) -> int:
    """执行 dry-run 报告的删除；返回成功删除数。幂等：重跑只删剩余孤儿。"""
    return delete_entries(client, bucket, report.authority_orphans) + delete_entries(
        client, bucket, report.staging_orphans
    )
