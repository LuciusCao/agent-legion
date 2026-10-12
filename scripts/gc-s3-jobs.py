#!/usr/bin/env python3
"""S3 jobs/ 与 jobs-staging/ 前缀及材料根前缀的孤儿对象 GC（#340/#1269）。dry-run 默认。

回收 job 删除路径永远看不到的对象：行已删但删除失败/未执行的 authority
对象、promote 中途失败的拷贝、结果报告丢失的 Worker staging 残留；以及材料
上传路径的孤儿（#1269 格子③）：「已上传后重试」留下的上一代 g{代次} key、
行已删但对象删除失败的残留。jobs/ 与 jobs-staging/ 的判定与删除逻辑见
server/app/services/s3_jobs_gc.py；材料根前缀（bucket 根排除两个 jobs 前缀）
的扫描在本文件（材料 key 形如 {workspace_id}/{hash|id}/g{代次}/{filename}，
存活对象必有 materials 行——presign 先建行后签发 URL——故与 jobs/ 同手法：
对照 materials.storage_key 清单行 + 宽限窗）。DB 侧对照
job_artifacts ∪ materials（须能连上本 worktree 的 Postgres）。

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


def _past_grace(entry, cutoff) -> bool:
    modified = entry.last_modified
    if modified.tzinfo is None:
        modified = modified.replace(tzinfo=UTC)
    return modified < cutoff


def scan_material_root_orphans(entry_lister, key_exists, *, grace_hours: float):
    """材料根前缀的孤儿扫描（#1269 格子③，与 jobs/ 前缀同手法）。

    材料 key 在 bucket 根，与 jobs/、jobs-staging/ 两个产物前缀平级：列举
    根前缀并排除二者后，DB（job_artifacts ∪ materials）无行且超宽限窗即
    孤儿。宽限窗与 authority 对象共用 ``--grace-hours``：presign 先建行后
    签发 URL，在途上传的对象必有行，宽限窗只兜「行刚删、对象删除在途」的
    短窗口。
    """
    from server.app.services.s3_jobs_gc import JOBS_PREFIX, STAGING_PREFIX

    cutoff = datetime.now(UTC) - timedelta(hours=grace_hours)
    candidates = [
        entry
        for entry in entry_lister("")
        if not entry.key.startswith((JOBS_PREFIX, STAGING_PREFIX))
    ]
    known = key_exists([entry.key for entry in candidates])
    return [e for e in candidates if e.key not in known and _past_grace(e, cutoff)]


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
        scan_orphans,
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
        kwargs["aws_access_key_id"] = settings.access_key
        kwargs["aws_secret_access_key"] = settings.secret_key
    client = boto3.client("s3", **kwargs)

    app_settings = load_settings()
    dsn = args.database_url or app_settings.database_url
    job_db = JobQueries(dsn, jobs_dir=app_settings.jobs_dir)

    def entry_lister(prefix: str):
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=settings.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield ObjectEntry(
                    key=obj["Key"],
                    last_modified=obj["LastModified"],
                    size_bytes=int(obj.get("Size", 0)),
                )

    # 材料根前缀（#1269 格子③）：与 jobs/ 同一 key_exists（内部缓存全量
    # key 集合，两个扫描共享一遍 DB 载入）。
    key_exists = make_db_key_existence(job_db)
    report = scan_orphans(
        entry_lister,
        key_exists,
        grace_hours=args.grace_hours,
        staging_grace_hours=args.staging_grace_hours,
    )
    material_orphans = scan_material_root_orphans(
        entry_lister,
        key_exists,
        grace_hours=args.grace_hours,
    )

    label = "apply" if args.apply else "dry-run"
    print(
        f"{label}: jobs/ 孤儿 {len(report.authority_orphans)} 个"
        f"（{sum(o.size_bytes for o in report.authority_orphans)} 字节）, "
        f"jobs-staging/ 孤儿 {len(report.staging_orphans)} 个"
        f"（{sum(o.size_bytes for o in report.staging_orphans)} 字节）, "
        f"材料根前缀孤儿 {len(material_orphans)} 个"
        f"（{sum(o.size_bytes for o in material_orphans)} 字节）"
    )
    if not args.apply:
        if report.count or material_orphans:
            print("加 --apply 执行删除（幂等可重跑）。")
        return 0

    # 删除前逐批新鲜反查（TOCTOU，#344 评审 P1）：不用扫描阶段的缓存
    # key 集合——扫描与删除之间并发 promote 重建同名 key 并建行时，
    # 缓存判定会误删新权威对象；反查同时对照 materials（撞名 workspace
    # 的材料 key 在删除前最后一道被救下）。材料根前缀孤儿并入 authority
    # 批（同一 DB 对照 + 批内反查语义）。
    def fresh_revalidate(keys: list[str]) -> set[str]:
        return job_db.existing_object_storage_keys(keys)

    report.authority_orphans.extend(material_orphans)
    result = apply_gc(client, settings.bucket, report, revalidate=fresh_revalidate)
    print(f"已删除 {result.deleted} 个孤儿对象")
    if result.skipped_revalidated:
        print(f"反查救下 {result.skipped_revalidated} 个对象（扫描后被重新引用，未删除）")
    print("（删除失败的对象下一轮扫描会重试）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
