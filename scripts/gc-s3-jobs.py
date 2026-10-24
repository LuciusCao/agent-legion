#!/usr/bin/env python3
"""S3 jobs/ 与 jobs-staging/ 前缀及材料根前缀的孤儿对象 GC（#340/#1269）。dry-run 默认。

回收 job 删除路径永远看不到的对象：行已删但删除失败/未执行的 authority
对象、promote 中途失败的拷贝、结果报告丢失的 Worker staging 残留；以及材料
上传路径的孤儿（#1269 格子③）：「已上传后重试」留下的上一代 g{代次} key、
行已删但对象删除失败的残留。jobs/ 与 jobs-staging/ 的判定与删除逻辑见
server/app/services/s3_jobs_gc.py；材料根前缀（bucket 根排除两个 jobs 前缀，
材料 key 形如 {workspace_id}/{hash|id}/v2g{代次}/m_{material_id}/{filename}）
的扫描、删除门禁与删除批见 server/app/services/s3_material_gc.py（#1314）：
单次使用 key 文法（#1279）的材料孤儿在对象年龄过 max(--grace-hours,
材料下限 1h) 后进入 --apply 删除批、删除前逐批新鲜反查；旧文法（可复用
key）材料孤儿只报告不删除。撞名 jobs-staging/ 前缀下的材料形态 key 按
--grace-hours（而非 --staging-grace-hours）宽限窗分流判定（#1322）——
staging 窗更长时窗内的撞名材料孤儿不再延迟回收。承重墙是写端「行先于对象」协议（presign 先建
行后签 URL；inline 与 demo seed 先落 uploading intent 行再 PUT）。
DB 侧对照 job_artifacts ∪ materials（须能连上本 worktree 的 Postgres）。

用法:
    uv run python scripts/gc-s3-jobs.py [--grace-hours N] [--staging-grace-hours N]
                                        [--apply] [--database-url ...]

退出码: 0 正常（含 0 个孤儿）；非 0 = endpoint/DB 不可达等。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


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
    )
    from server.app.services.s3_material_gc import (
        MATERIAL_MIN_GRACE_HOURS,
        apply_material_gc,
        collect_gc_report,
    )
    from server.app.settings import load_settings
    from server.app.storage import load_s3_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--grace-hours",
        type=float,
        default=DEFAULT_GRACE_HOURS,
        help=(
            f"authority 与材料对象的宽限窗（小时，默认 {DEFAULT_GRACE_HOURS}；材料删除批"
            f"另有 {MATERIAL_MIN_GRACE_HOURS}h 对象年龄下限，取大）。"
        ),
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

    # jobs 两条道与材料根前缀共用一个 key_exists（内部缓存全量 key 集合，
    # 一遍 DB 载入）；撞名前缀里的材料形态 key 分流进材料道（#1282 r4 P1）。
    report, split = collect_gc_report(
        entry_lister,
        make_db_key_existence(job_db),
        grace_hours=args.grace_hours,
        staging_grace_hours=args.staging_grace_hours,
    )

    def _stat(entries: list) -> str:
        return f"{len(entries)} 个（{sum(o.size_bytes for o in entries)} 字节）"

    label = "apply" if args.apply else "dry-run"
    au, st = report.authority_orphans, report.staging_orphans
    print(f"{label}: jobs/ 孤儿 {_stat(au)}, jobs-staging/ 孤儿 {_stat(st)}")
    print(
        f"{label}: 材料孤儿 {_stat(split.deletable)}进删除批；"
        f"未满材料年龄下限暂缓 {len(split.deferred)} 个；"
        f"旧文法材料孤儿 {_stat(split.report_only)}只报告不删除"
    )
    if not args.apply:
        if report.count or split.deletable or split.report_only:
            print(
                "加 --apply 执行删除（幂等可重跑；"
                "legacy 固定 authority key 与旧文法材料孤儿不删）。"
            )
        return 0

    # 删除前逐批新鲜反查（TOCTOU，#344 评审 P1）：不用扫描阶段的缓存
    # key 集合——扫描与删除之间并发 promote 重建同名 key 并建行时，
    # 缓存判定会误删新权威对象；反查同时对照 materials（撞名 workspace
    # 的材料 key 与行刚重建的材料 key 在删除前最后一道被救下）。
    def fresh_revalidate(keys: list[str]) -> set[str]:
        return job_db.existing_object_storage_keys(keys)

    result = apply_gc(client, settings.bucket, report, revalidate=fresh_revalidate)
    material = apply_material_gc(
        client, settings.bucket, split.deletable, revalidate=fresh_revalidate
    )
    print(
        f"已删除 {result.deleted + material.deleted} 个孤儿对象（其中材料 {material.deleted} 个）"
    )
    if material.withheld:
        print(f"材料删除门禁扣下 {material.withheld} 个对象（删除前复核未通过，未删除）")
    saved = result.skipped_revalidated + material.skipped_revalidated
    if saved:
        print(f"反查救下 {saved} 个对象（扫描后被重新引用，未删除）")
    print("（删除失败的对象下一轮扫描会重试）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
