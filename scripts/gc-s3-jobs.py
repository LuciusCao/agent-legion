#!/usr/bin/env python3
"""S3 jobs/ 与 jobs-staging/ 前缀及材料根前缀的孤儿对象 GC（#340/#1269/#1279）。dry-run 默认。

回收 job 删除路径永远看不到的对象：行已删但删除失败/未执行的 authority
对象、promote 中途失败的拷贝、结果报告丢失的 Worker staging 残留；以及材料
上传路径的孤儿（#1269 格子③）：「已上传后重试」留下的上一代 g{代次} key、
行已删但对象删除失败的残留。jobs/ 与 jobs-staging/ 的判定与删除逻辑见
server/app/services/s3_jobs_gc.py；材料根前缀（bucket 根排除两个 jobs 前缀）
的扫描在本文件（材料 key 形如 {workspace_id}/{hash|id}/v2g{代次}/m_{material_id}/{filename}，
存活对象必有 materials 行——presign 先建行后签发 URL——故与 jobs/ 同手法：
对照 materials.storage_key 清单行 + 宽限窗）。
#1279 起材料 key 新文法第 3 段为 v2g{代次}（写端不可注入的服务端派生段）
+ m_ 标记的 material_id 唯一段：删行后同文件同名再上传必得新 key，跨行
不可复用，check-then-delete 窗口构造性关闭——新文法孤儿重新并入 --apply
删除批（与 jobs/ 同走删除前新鲜反查）；旧文法孤儿（g{代次} 段形态，含
32-hex / m_ 前缀首段文件名的歧义形态）保持只报告，待 bucket
lifecycle/TTL 排空。文法门禁判定是
s3_jobs_gc.material_key_single_use（删除动作内部还有一道同样的纵深闸门）。
DB 侧对照 job_artifacts ∪ materials（须能连上本 worktree 的 Postgres）。

用法:
    uv run python scripts/gc-s3-jobs.py [--grace-hours N] [--staging-grace-hours N]
                                        [--apply] [--database-url ...]

退出码: 0 正常（含 0 个孤儿）；非 0 = endpoint/DB 不可达等。
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

# 材料形态文法（第 3 段 (v2)g{代次}）的权威实现是
# server/app/services/s3_jobs_gc.py 的 material_shaped_key（#1295 下沉：
# 扫描期的 /.rollback/ 豁免判定也要用它排除材料形态 key）；单次使用
# 文法门禁（v2g 代次段 + m_ 标记的 material_id 唯一段，#1279）的权威
# 实现是同模块的 material_key_single_use——本文件的删除批分流与三条
# key 产线（materials.py / run_text_batch.py / demo_material_seed.py）
# 的文法必须同步演进。


def scan_material_root_orphans(entry_lister, key_exists, grace_hours: float):
    """材料根前缀的孤儿扫描（#1269 格子③，与 jobs/ 前缀同手法）。

    材料 key 在 bucket 根，与 jobs/、jobs-staging/ 两个产物前缀平级：列举
    根前缀并排除二者后，DB（job_artifacts ∪ materials）无行且超宽限窗即
    孤儿。宽限窗与 authority 对象共用 ``--grace-hours``：presign 先建行后
    签发 URL，在途上传的对象必有行，宽限窗只兜「行刚删、对象删除在途」的
    短窗口。判定按 KEY_PAGE_SIZE 分页推进、只累计孤儿（#1287）：全量对象
    清单不驻留内存，大 bucket 上不会比 scan_orphans 多一份完整清单。页内
    判定复用 s3_jobs_gc.orphans_in_page（与 jobs/ 前缀同一判定）。
    """
    from server.app.services import s3_jobs_gc

    cutoff = datetime.now(UTC) - timedelta(hours=grace_hours)
    orphans, pending = [], []
    for entry in entry_lister(""):
        if entry.key.startswith((s3_jobs_gc.JOBS_PREFIX, s3_jobs_gc.STAGING_PREFIX)):
            continue
        pending.append(entry)
        if len(pending) >= s3_jobs_gc.KEY_PAGE_SIZE:
            orphans.extend(s3_jobs_gc.orphans_in_page(pending, key_exists, cutoff))
            pending = []
    if pending:
        orphans.extend(s3_jobs_gc.orphans_in_page(pending, key_exists, cutoff))
    return orphans


def main() -> int:
    from dotenv import load_dotenv

    # 显式传路径：无参 load_dotenv() 走调用栈探测，子进程场景不可靠。
    load_dotenv(Path(ROOT / ".env"), override=False)

    import boto3

    from server.app.jobs.queries import JobQueries
    from server.app.services.s3_jobs_gc import (
        DEFAULT_GRACE_HOURS,
        DEFAULT_STAGING_GRACE_HOURS,
        ObjectEntry,
        apply_gc,
        make_db_key_existence,
        material_shaped_key,
        scan_orphans,
        split_material_orphans,
    )
    from server.app.settings import load_settings
    from server.app.storage import load_s3_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grace-hours",
        type=float,
        default=DEFAULT_GRACE_HOURS,
        help=f"authority 对象的宽限窗（小时，默认 {DEFAULT_GRACE_HOURS}）。",
    )
    parser.add_argument(
        "--staging-grace-hours",
        type=float,
        default=DEFAULT_STAGING_GRACE_HOURS,
        help=f"staging 对象的宽限窗（小时，默认 {DEFAULT_STAGING_GRACE_HOURS}）。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行删除（默认 dry-run 只统计）。",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="覆盖 AGENT_LEGION_DATABASE_URL（对照 job_artifacts 与 materials 用）。",
    )
    args = parser.parse_args()

    settings = load_s3_settings()
    if settings is None:
        print("提示: AGENT_LEGION_S3_BUCKET 未配置，跳过 S3 孤儿对象 GC")
        return 0
    kwargs: dict[str, str] = {"region_name": settings.region}
    if settings.endpoint_url:
        kwargs["endpoint_url"] = settings.endpoint_url
    if settings.access_key:
        kwargs.update(
            aws_access_key_id=settings.access_key, aws_secret_access_key=settings.secret_key
        )
    client = boto3.client("s3", **kwargs)

    app_settings = load_settings()
    job_db = JobQueries(
        args.database_url or app_settings.database_url, jobs_dir=app_settings.jobs_dir
    )

    def entry_lister(prefix: str):
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield ObjectEntry(obj["Key"], obj["LastModified"], int(obj.get("Size", 0)))

    # 材料根前缀（#1269 格子③）：与 jobs/ 同一 key_exists（内部缓存全量
    # key 集合，两个扫描共享一遍 DB 载入）。
    key_exists = make_db_key_existence(job_db)
    staging_grace = args.staging_grace_hours
    report = scan_orphans(
        entry_lister, key_exists, grace_hours=args.grace_hours, staging_grace_hours=staging_grace
    )
    material_orphans = scan_material_root_orphans(entry_lister, key_exists, args.grace_hours)
    # 碰撞命名空间分流（#1282 r4 P1；族不变量：删除批只收不可复用 key）：
    # jobs/ 与 jobs-staging/ 前缀里材料形态的 key 是撞名 workspace 的材料，
    # 与材料根前缀同理按单次使用文法分流，并入材料孤儿集合。含 .rollback
    # 段的材料形态 key 自 #1295 起不被扫描期豁免吞掉（s3_jobs_gc 的
    # _rollback_backup_exempt 叠加材料形态排除），能到达这里。
    collided = [
        o for o in report.authority_orphans + report.staging_orphans if material_shaped_key(o.key)
    ]
    report.authority_orphans = [
        o for o in report.authority_orphans if not material_shaped_key(o.key)
    ]
    report.staging_orphans = [o for o in report.staging_orphans if not material_shaped_key(o.key)]
    material_orphans += collided

    # #1279：单次使用文法（v2g 代次段 + m_ 标记的 material_id 唯一段）的
    # 材料孤儿重新并入删除批（跨行不可复用，check-then-delete 窗口构造性
    # 关闭；删除前仍走新鲜反查兜底）；旧文法（g 段形态）孤儿保持只报告
    # ——旧写端仍可复用其 key，排空交给 bucket lifecycle/TTL。
    report.material_orphans, legacy = split_material_orphans(material_orphans)

    def _stat(entries: list) -> str:
        return f"{len(entries)} 个（{sum(o.size_bytes for o in entries)} 字节）"

    label = "apply" if args.apply else "dry-run"
    au, st = report.authority_orphans, report.staging_orphans
    print(f"{label}: jobs/ 孤儿 {_stat(au)}, jobs-staging/ 孤儿 {_stat(st)}")
    print(
        f"{label}: 材料孤儿 {_stat(report.material_orphans)}（进删除批）, 旧文法材料孤儿 {_stat(legacy)}（只报告不删除）"
    )
    if not args.apply:
        if report.count or legacy:
            print("加 --apply 执行删除（幂等可重跑；旧文法材料孤儿不删）。")
        return 0

    # 删除前逐批新鲜反查（TOCTOU，#344 评审 P1）：不用扫描阶段的缓存
    # key 集合——扫描与删除之间并发 promote 重建同名 key 并建行时，
    # 缓存判定会误删新权威对象；反查同时对照 materials（撞名 workspace
    # 的材料 key 与行刚重建的材料 key 在删除前最后一道被救下）。
    def fresh_revalidate(keys: list[str]) -> set[str]:
        return job_db.existing_object_storage_keys(keys)

    result = apply_gc(client, settings.bucket, report, revalidate=fresh_revalidate)
    print(f"已删除 {result.deleted} 个孤儿对象")
    if result.skipped_revalidated:
        print(f"反查救下 {result.skipped_revalidated} 个对象（扫描后被重新引用，未删除）")
    print("（删除失败的对象下一轮扫描会重试）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
