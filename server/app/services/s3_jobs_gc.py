"""S3 ``jobs/`` 与 ``jobs-staging/`` 前缀的孤儿对象扫描与回收（#340）。

job 删除路径（``job_deletion`` → ``job_artifacts.delete_objects``）只按
``job_artifacts.storage_key`` 行驱动删除，失败不重试；``promote_all`` 在
行写入失败时会留下已拷贝的 authority 对象；Worker 直传 staging 后结果
报告丢失则 staging 对象永久滞留。这些对象对应用不可见，只能按前缀列举
对照回收：

- ``jobs/{ws}/{job_id}/{name}``：key 不在 ``job_artifacts.storage_key``
  集合中且 LastModified 超过宽限窗 → 孤儿；
- ``jobs-staging/{ws}/{job_id}/{exec_id}/{name}``：超宽限窗即回收候选
  （Worker 直传的 staging 源只在 finish 提交后由完成方删除——失败/冲
  突/闸拒/进程崩溃的残留走本工具或 bucket lifecycle；docs/materials-
  storage-deployment.md 规划的 1 天短保留）。``/.rollback/`` 段的
  promote 回滚备份例外豁免（仅 jobs/ 与 jobs-staging/ 前缀的 key，
  #1285——材料文件名可含合法 .rollback 段，材料根前缀 key 不适用；
  #1295——撞名前缀下的材料形态 key 同样不适用，豁免判定叠加材料形态
  排除，见 ``_rollback_backup_exempt``）：
  恢复最终失败时它是旧字节的最后恢复源
  （codex #774 P1 族），正常结局的备份由 promote 自身清理、不经过本
  工具。

设计约束：
- 判定核心（``scan_orphans``）只依赖注入的列举器与 key 存在性函数，
  单测不碰 boto3 / DB；
- ``job_artifacts.storage_key`` 无索引：按 key 反查会退化为每页一次
  全表扫描，故存在性判定单遍全量载入构建内存 set（运维 CLI 场景的
  有意取舍）；
- 删除分批（每批 ``DELETE_BATCH`` 个），幂等可重跑。

材料根前缀（及撞名前缀里的材料形态 key）的扫描与删除门禁在
``s3_material_gc``（#1314）；本模块的 ``apply_gc`` 不删材料形态 key。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
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
    """dry-run 与 --apply 共用的扫描结果。

    staging_orphans 里可能混有撞名前缀的材料形态 key——它们按材料宽限窗
    （而非 staging 宽限窗）判定（#1322），由
    ``s3_material_gc.collect_gc_report`` 从车道分流进材料候选；
    ``apply_gc`` 不删材料形态 key（只经 s3_material_gc 的门禁删除）。
    """

    authority_orphans: list[ObjectEntry]
    staging_orphans: list[ObjectEntry]

    @property
    def count(self) -> int:
        return len(self.authority_orphans) + len(self.staging_orphans)

    @property
    def total_bytes(self) -> int:
        entries = (*self.authority_orphans, *self.staging_orphans)
        return sum(o.size_bytes for o in entries)


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

    两个前缀都经 ``key_exists`` 对照（系统性评审 P1，#344）：materials
    的 key 以 workspace id 起手（``{workspace_id}/{hash|id}/v2g{代次}/
    m_{material_id}/{filename}``，#1279 前的在役形态第 3 段为 g{代次}），
    workspace id 允许叫
    ``jobs`` / ``jobs-staging``（无保留名约束），那样的材料 key 会落进
    这两个前缀——staging 不能只看宽限窗无条件回收，否则撞名 workspace
    的材料会被整批误删。撞名 ``jobs-staging/`` 前缀下的材料形态 key 按
    材料宽限窗（``grace_hours`` 截止点，与材料根前缀扫描同源）判定
    （#1322）——staging 宽限窗只约束真正的 staging 对象，staging 窗比
    材料窗更长时窗内的撞名材料孤儿不再被 staging 截止点吞掉（延迟回
    收）；它们随 staging_orphans 返回，由 s3_material_gc.collect_gc_report
    分流进材料候选。authority 侧按页攒批只是有界化每次传给
    ``key_exists`` 的 key 列表；两个前缀在 ListObjectsV2 上互不重叠
    （第 6 个字符 ``/`` vs ``-``）。
    """
    now = now or datetime.now(UTC)
    report = OrphanReport([], [])
    # 材料形态 key 在 staging 道按材料宽限窗判定（#1322）；与 authority
    # 道/材料根前缀扫描同一截止点。
    staging_page = partial(_staging_orphans_in, material_cutoff=now - timedelta(hours=grace_hours))
    lanes = (
        (STAGING_PREFIX, staging_grace_hours, staging_page, report.staging_orphans),
        (JOBS_PREFIX, grace_hours, orphans_in_page, report.authority_orphans),
    )
    for prefix, grace, page_orphans, sink in lanes:
        cutoff = now - timedelta(hours=grace)
        pending: list[ObjectEntry] = []
        for entry in lister(prefix):
            pending.append(entry)
            if len(pending) >= KEY_PAGE_SIZE:
                sink.extend(page_orphans(pending, key_exists, cutoff))
                pending = []
        if pending:
            sink.extend(page_orphans(pending, key_exists, cutoff))
    return report


def orphans_in_page(
    entries: list[ObjectEntry], key_exists: KeyExistence, cutoff: datetime
) -> list[ObjectEntry]:
    """DB 无行且超宽限窗：窗内的未知 key 保留（上传在途/行未写场景）。

    页内判定的共享实现：jobs/ 前缀的 authority 页与材料根前缀页
    （scripts/gc-s3-jobs.py 的 scan_material_root_orphans，#1287）同一
    判定；staging 页另有带 ``/.rollback/`` 豁免的变体
    （``_staging_orphans_in``）。
    """
    known = key_exists([e.key for e in entries])
    return [e for e in entries if e.key not in known and _past_grace(e, cutoff)]


def _material_generation(segments: list[str]) -> str:
    """材料 key 第 3 段的代次数字：``g{n}``（#1267 旧文法）或 ``v2g{n}``
    （#1279 现文法）；非材料形态（段数不足/段形不符）返回 ''。"""
    seg = segments[2] if len(segments) >= 4 else ""
    return seg[3:] if seg.startswith("v2g") else seg[1:] if seg.startswith("g") else ""


def material_shaped_key(key: str) -> bool:
    """碰撞命名空间里的材料 key 形态（#1282 r4 P1）：第 3 段是 g{代次} 或
    v2g{代次}（#1279）。

    材料 key 为 ``{workspace_id}/{hash|material_id}/(v2)g{代次}/[m_{material_id}/]{filename}``
    （两种文法本判定都匹配——碰撞分流要收全形态，删除门禁在
    ``material_key_single_use``）；workspace id 可字面为 jobs / jobs-staging
    （无保留名约束），那样的材料 key 落在 jobs 前缀扫描内。真 job 产物同
    位置是 job id（``{ws}_{workflow_key}_{source_id}``，恒含下划线），永不
    匹配 (v2)g{纯数字}——分流零误伤。legacy 无代次段的材料 key 不可复用
    （新上传恒带代次段），留在删除批安全。
    """
    return _material_generation(key.split("/")).isdigit()


def material_key_single_use(key: str) -> bool:
    """#1279 新文法的材料 key 跨行不可复用——材料删除批的准入门禁
    （#1314：``s3_material_gc`` 的分流与删除动作处复核；不满足的材料
    孤儿只报告不删除）。

    形态：``{ws}/{hash|id}/v2g{代次}/m_{material_id}/{filename}``。删行后
    同文件同名再上传也必得新 key，revalidate→删 窗口随之构造性关闭。
    旧文法（g 段非 v2g 形态、legacy 无代次段 key、inline-{uuid4} key）
    一律排除。本判定与 materials.py（presign/重试）、material_repair.py、
    material_object_publish.py（inline/demo seed 共用的写入 chokepoint）
    的 key 文法（以及 ``material_shaped_key`` 碰撞分流面）必须同步演进。

    构造性关闭论证（#1279 codex r1/r2 P1 族——「第 4 段标记」连续两轮被
    「旧文法合法文件名巧合」反例击穿后，标记移入写端不可注入的第 3 段；
    #1314 重开删除批时此论证是承重墙）：

    1. 逐条核验在役旧写端产线的第 3 段能力：presign 首发与重试
       （materials.py）第 3 段是服务端派生的字面量 ``'g' || generation``
       ——#1267 起恒为 ``g<int>``；inline（run_text_batch）旧形态第 3 段
       是 ``inline-{uuid4}``；demo seed 旧形态无代次段（第 3 段是文件名
       首段）。没有任何在役写端能在第 3 段写出 ``v2g0``。
    2. 混合版本窗口内的活写端只有紧邻旧版 Host server（材料 key 写端全
       在 Host 单副本后端：presign/inline/demo seed 都在 server 侧，
       worker/ 零写端）——它受 #1267 文法约束，第 3 段发不出 ``v2g``；
       新版 GC CLI 与旧 Host 并行时，旧 Host 产出的全部 key 都被本门禁
       拦在只报告侧。例外是 #1312 期 Host：已发 ``v2g`` key 但 inline/demo
       seed 仍先 PUT 后建行——秒级窗口由材料年龄下限兜住（#1314）。
    3. #1267 之前的 legacy 写端已死（不存在于任何与现行代码的混合部署）；
       其桶内遗留即使有形态巧合（如文件名首段字面 ``v2g0``），有行则非
       孤儿（key_exists/新鲜反查救下），无行则是无人能重建引用的真孤儿
       ——删除安全。
    4. 因此「fresh_revalidate 后旧写端重建同名 key + PUT」对带 v2g 标记
       的 key 构造性不成立：重建者只能是现版写端，而现版写端对新行恒
       生成新 material_id、必得新 key。
    5. 现版写端行先于对象（#1314）：key 先被已提交的行（含 uploading
       intent）引用、后 PUT——反查「无行」即该 key 此后永不再被引用。
    """
    segments = key.split("/")
    mid = segments[3] if len(segments) >= 5 else ""
    new_gen = len(segments) >= 5 and segments[2].startswith("v2g")
    hex_ok = mid[:2] == "m_" and len(mid) == 34 and all(c in "0123456789abcdef" for c in mid[2:])
    return new_gen and _material_generation(segments).isdigit() and hex_ok


def _rollback_backup_exempt(key: str) -> bool:
    """``/.rollback/`` 回滚备份豁免的判定（codex #774 P1 族，#1285/#1295 收窄）。

    豁免仅对 jobs/ 与 jobs-staging/ 前缀的 key 生效——promote 回滚备份只
    出现在这两个产物前缀下（恢复最终失败时它是幸存清单行所指向旧字节的
    最后恢复源）。两类 key 不适用：

    - 材料根前缀的 key（#1285）：材料文件名允许合法多段相对路径、无
      .rollback 保留段，``{ws}/{hash}/g{代次}/dir/.rollback/f.bin`` 形态
      的旧代次/删行残留若继承本豁免，会在材料孤儿删除批重开（#1314）
      后被永久跳过；
    - 撞名前缀下的材料形态 key（#1295）：workspace id 字面为
      jobs / jobs-staging 时材料 key 落在产物前缀内，同样允许合法
      .rollback 段；若豁免先于材料形态分流命中，该 key 既不删除也不进
      报告——永久不可见。豁免前先识别材料形态（第 3 段 (v2)g{代次}）排除
      之，staging 道扫描期按材料宽限窗分流进材料候选（#1322，收口纪律
      见 #1282）。
    """
    return (
        key.startswith((JOBS_PREFIX, STAGING_PREFIX))
        and "/.rollback/" in key
        and not material_shaped_key(key)
    )


def _staging_orphans_in(
    entries: list[ObjectEntry],
    key_exists: KeyExistence,
    cutoff: datetime,
    material_cutoff: datetime,
) -> list[ObjectEntry]:
    """超宽限窗且 DB 无行：staging 的 DB 对照救的是撞名 workspace 的
    materials（正常 staging 对象本就无行，宽限窗是它们的唯一防线）。
    材料形态 key 按 ``material_cutoff``（材料宽限窗截止点，与材料根前缀
    扫描同一 ``--grace-hours`` 窗口）判定，真正的 staging 对象按
    ``cutoff``（staging 宽限窗截止点）判定（#1322，#1318 codex r1 P2）：
    staging 窗比材料窗更长时，窗内的撞名材料孤儿不再被 staging 截止点
    吞掉而延迟回收；未满材料宽限窗的材料形态 key 与根前缀扫描同样不进
    候选（窗内保留）。``/.rollback/`` 段的回滚备份按
    ``_rollback_backup_exempt`` 豁免
    （codex #774 P1 族）：promote 恢复最终失败时被刻意保留的备份是幸存
    清单行所指向旧字节的最后恢复源，其生命周期归运维处置（ERROR 日志带
    key）；代价是崩溃残留的 `.rollback` 孤儿不再被本 GC 回收（备份只在
    authority 已存在时创建、崩溃窗口毫秒级，量可忽略）。豁免判定叠加材
    料形态排除（#1295）：撞名 workspace 的材料 key 文件名可含合法
    .rollback 段，不豁免——进孤儿报告由 collect_gc_report 分流到材料
    候选，避免被豁免吞掉后永久不可见。bucket lifecycle
    的子串不可豁免性见 docs/materials-storage-deployment.md。"""
    known = key_exists([e.key for e in entries])
    return [
        entry
        for entry in entries
        if entry.key not in known
        and not _rollback_backup_exempt(entry.key)
        and _past_grace(entry, material_cutoff if material_shaped_key(entry.key) else cutoff)
    ]


def make_db_key_existence(queries: JobQueries) -> KeyExistence:
    """DB 侧存在性判定：首次调用时单遍全量载入对象存储 key 集合
    （job_artifacts ∪ materials，BOUNDARY-DATA-001：service 层不落 SQL
    字面量）。两列都无索引，按 key 反查会退化为每页一次全表扫描；
    单遍顺序扫描 + 内存 set 是运维 CLI 的有意取舍。"""

    all_keys: set[str] | None = None

    def exists(keys: list[str]) -> set[str]:
        nonlocal all_keys
        if all_keys is None:
            all_keys = queries.all_object_storage_keys()
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


@dataclass
class ApplyResult:
    """``apply_gc`` 的结果：删除数 + 被 revalidate 救下的对象数。"""

    deleted: int
    skipped_revalidated: int = 0


def apply_gc(
    client: BatchDeleter,
    bucket: str,
    report: OrphanReport,
    revalidate: KeyExistence | None = None,
) -> ApplyResult:
    """执行 dry-run 报告的删除；返回删除数与被反查救下的对象数。
    幂等：重跑只删剩余孤儿。

    TOCTOU 防护（#344 评审 P1）：``revalidate`` 提供时，每个删除批
    （authority 与 staging 都校验——staging 校验救的是撞名 workspace 的
    materials，P1 修复的一部分）在删除前用**新鲜** DB 反查过滤——扫描与
    删除之间并发 promote 可能重建同名 key 并建行，按扫描阶段缓存的孤儿
    判定删会把新权威对象删掉，DB 清单从此指向缺失内容。被过滤掉的 key
    计入 ``skipped_revalidated``，CLI 会如实报告，运维才能区分「删完了」
    与「跳过了仍被引用的对象」。
    """
    deleted = 0
    skipped = 0
    # 纵深防御（#774 对抗复审）：回滚备份在任何路径都不可删——正常报告来
    # 自 scan 层的豁免，但手工构造的 OrphanReport 会绕过它；把「GC 永不删
    # 恢复备份」钉在删除动作本身上。豁免按 _rollback_backup_exempt 收窄到
    # jobs/ 与 jobs-staging/ 前缀（#1285）且排除材料形态 key（#1295）：材
    # 料 key 的文件名可含合法 .rollback 段（dir/.rollback/file.bin），材料
    # 删除批（#1314）里不得继承本豁免被永久跳过。材料形态 key 一律不在
    # 本函数删除（#1314）：它们只经 s3_material_gc 的门禁（单次使用文法 +
    # 年龄下限 + 强制反查）删除，手工构造的报告也绕不过。
    authority_orphans = [e for e in report.authority_orphans if _jobs_lane_deletable(e.key)]
    staging_orphans = [e for e in report.staging_orphans if _jobs_lane_deletable(e.key)]
    if revalidate is not None:
        for entries in (authority_orphans, staging_orphans):
            for batch in _batches(entries):
                known = revalidate([e.key for e in batch])
                still_orphans = [e for e in batch if e.key not in known]
                skipped += len(batch) - len(still_orphans)
                if still_orphans:
                    deleted += _delete_batch(client, bucket, still_orphans)
    else:
        for entries in (authority_orphans, staging_orphans):
            deleted += delete_entries(client, bucket, entries)
    return ApplyResult(deleted=deleted, skipped_revalidated=skipped)


def _jobs_lane_deletable(key: str) -> bool:
    return not _rollback_backup_exempt(key) and not material_shaped_key(key)


def _batches(entries: list[ObjectEntry]) -> Iterator[list[ObjectEntry]]:
    for i in range(0, len(entries), DELETE_BATCH):
        yield entries[i : i + DELETE_BATCH]
