"""S3 材料根前缀孤儿的扫描、删除门禁与删除批（#1269/#1314）。

材料 key 在 bucket 根（``{ws}/{hash|id}/v2g{代次}/m_{material_id}/{filename}``），
与 ``jobs/``、``jobs-staging/`` 平级；workspace id 字面为 jobs / jobs-staging
时材料 key 落进两个产物前缀（撞名命名空间，#1282 r4 P1），按材料形态文法分流
回材料集合——撞名 ``jobs-staging/`` 前缀下的材料形态 key 在扫描期按材料宽限窗
（非 staging 宽限窗）判定分流，staging 窗更长时不再延迟回收（#1322）。
jobs 两条道的判定与删除在 ``s3_jobs_gc``（其 ``apply_gc`` 不删
材料形态 key），本模块是材料道：

- 准入三道门：①``material_key_single_use``（#1279 单次使用文法，可复用 key
  的先查后删关不死窗口——旧文法只报告）；②对象年龄过
  ``max(--grace-hours, MATERIAL_MIN_GRACE_HOURS)``，删除动作处按当时时钟
  复核；③每批删除前新鲜反查无行（无反查整道不删）。
- 承重墙是写端「行先于对象」协议：presign/重试/repair 在行锁内派生 key 并
  提交后才签 URL；inline 与 demo seed 经 ``material_object_publish`` 先落
  uploading intent 行再 PUT——反查「无行」即该 key 此后永不再被引用（id 不
  复用、代次单调）。年龄下限是纵深，兜住协议之外仍「先 PUT 后建行」的
  #1312 期旧 Host（秒级窗口）与 S3/GC 主机时钟偏差。

逐格交错决策表见 docs/architecture/materials-and-runs-design.md §6.4.2。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from server.app.services import s3_jobs_gc
from server.app.services.s3_jobs_gc import (
    BatchDeleter,
    KeyExistence,
    Lister,
    ObjectEntry,
    OrphanReport,
    material_key_single_use,
    material_shaped_key,
)

MATERIAL_MIN_GRACE_HOURS = 1.0


@dataclass
class MaterialOrphanSplit:
    """材料孤儿候选三分：进删除批 / 未满年龄下限暂缓 / 旧文法只报告。"""

    deletable: list[ObjectEntry]
    deferred: list[ObjectEntry]
    report_only: list[ObjectEntry]


@dataclass
class MaterialApplyResult:
    deleted: int = 0
    skipped_revalidated: int = 0
    # 删除动作处门禁扣下的（文法不符 / 年龄未过下限 / 未提供反查）。
    withheld: int = 0


def material_cutoff(grace_hours: float, now: datetime) -> datetime:
    """材料道的年龄截止点：``--grace-hours`` 与下限取大。"""
    return now - timedelta(hours=max(grace_hours, MATERIAL_MIN_GRACE_HOURS))


def scan_material_root_orphans(
    entry_lister: Lister, key_exists: KeyExistence, grace_hours: float
) -> list[ObjectEntry]:
    """材料根前缀的孤儿扫描（#1269 格子③，与 jobs/ 前缀同手法）。

    列举根前缀并排除两个 jobs 前缀后，DB（job_artifacts ∪ materials）无行且
    超 ``--grace-hours`` 即孤儿候选（门禁见 ``partition_material_orphans``）。
    判定按 KEY_PAGE_SIZE 分页推进、只累计孤儿（#1287）：全量对象清单不驻留
    内存；页内判定复用 ``s3_jobs_gc.orphans_in_page``。
    """
    cutoff = datetime.now(UTC) - timedelta(hours=grace_hours)
    prefixes = (s3_jobs_gc.JOBS_PREFIX, s3_jobs_gc.STAGING_PREFIX)
    orphans: list[ObjectEntry] = []
    pending: list[ObjectEntry] = []
    for entry in entry_lister(""):
        if entry.key.startswith(prefixes):
            continue
        pending.append(entry)
        if len(pending) >= s3_jobs_gc.KEY_PAGE_SIZE:
            orphans.extend(s3_jobs_gc.orphans_in_page(pending, key_exists, cutoff))
            pending = []
    if pending:
        orphans.extend(s3_jobs_gc.orphans_in_page(pending, key_exists, cutoff))
    return orphans


def partition_material_orphans(
    entries: Iterable[ObjectEntry], *, grace_hours: float, now: datetime | None = None
) -> MaterialOrphanSplit:
    """候选（DB 无行、已过 ``--grace-hours``）按删除门禁三分。"""
    cutoff = material_cutoff(grace_hours, now or datetime.now(UTC))
    split = MaterialOrphanSplit([], [], [])
    for entry in entries:
        if not material_key_single_use(entry.key):
            split.report_only.append(entry)
        elif s3_jobs_gc._past_grace(entry, cutoff):
            split.deletable.append(entry)
        else:
            split.deferred.append(entry)
    return split


def collect_gc_report(
    entry_lister: Lister,
    key_exists: KeyExistence,
    *,
    grace_hours: float,
    staging_grace_hours: float,
) -> tuple[OrphanReport, MaterialOrphanSplit]:
    """扫描全部前缀：jobs 两条道的报告（已剔除撞名的材料形态 key）+ 材料
    候选三分。dry-run 与 --apply 共用（scripts/gc-s3-jobs.py）。"""
    report = s3_jobs_gc.scan_orphans(
        entry_lister, key_exists, grace_hours=grace_hours, staging_grace_hours=staging_grace_hours
    )
    candidates = scan_material_root_orphans(entry_lister, key_exists, grace_hours)
    # 撞名命名空间分流（#1282 r4 P1）：staging 道的材料形态 key 自 #1322 起
    # 在 scan_orphans 页内按材料宽限窗（而非 staging 宽限窗）判定——
    # staging 窗比材料窗更长时，窗内的撞名材料孤儿不再被 staging 截止点
    # 吞掉而延迟回收；含 .rollback 段的材料形态 key 自
    # #1295 起不被扫描期豁免吞掉，同样到达这里。
    for lane in (report.authority_orphans, report.staging_orphans):
        candidates.extend(e for e in lane if material_shaped_key(e.key))
        lane[:] = [e for e in lane if not material_shaped_key(e.key)]
    return report, partition_material_orphans(candidates, grace_hours=grace_hours)


def apply_material_gc(
    client: BatchDeleter,
    bucket: str,
    entries: list[ObjectEntry],
    revalidate: KeyExistence | None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> MaterialApplyResult:
    """删除材料道（门禁钉在删除动作本身：手工构造的列表同样受约束）。

    无 ``revalidate`` → 整道不删；每批删除前按 ``clock()`` 复核单次使用文法
    与年龄下限，再新鲜反查，只删此刻仍无行的 key。
    """
    result = MaterialApplyResult()
    if revalidate is None:
        result.withheld = len(entries)
        return result
    now = clock or (lambda: datetime.now(UTC))
    for batch in s3_jobs_gc._batches(entries):
        cutoff = material_cutoff(0.0, now())
        eligible = [
            e for e in batch if material_key_single_use(e.key) and s3_jobs_gc._past_grace(e, cutoff)
        ]
        result.withheld += len(batch) - len(eligible)
        known = revalidate([e.key for e in eligible]) if eligible else set()
        still_orphans = [e for e in eligible if e.key not in known]
        result.skipped_revalidated += len(eligible) - len(still_orphans)
        if still_orphans:
            result.deleted += s3_jobs_gc._delete_batch(client, bucket, still_orphans)
    return result
