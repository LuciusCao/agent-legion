#!/usr/bin/env python3
"""S3 jobs/ 与 jobs-staging/ 前缀的孤儿对象 GC（#340）。dry-run 默认。

回收 job 删除路径永远看不到的对象：行已删但删除失败/未执行的 authority
对象、promote 中途失败的拷贝、结果报告丢失的 Worker staging 残留。
判定与删除逻辑见 server/app/services/s3_jobs_gc.py；DB 侧对照
job_artifacts.storage_key（须能连上本 worktree 的 Postgres）。

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
        help="覆盖 AGENT_LEGION_DATABASE_URL（对照 job_artifacts 用）。",
    )
    args = parser.parse_args()

    settings = load_s3_settings()
    if settings is None:
        print("提示: AGENT_LEGION_S3_BUCKET 未配置，跳过 S3 jobs GC")
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

    report = scan_orphans(
        entry_lister,
        make_db_key_existence(job_db),
        grace_hours=args.grace_hours,
        staging_grace_hours=args.staging_grace_hours,
    )

    label = "apply" if args.apply else "dry-run"
    print(
        f"{label}: jobs/ 孤儿 {len(report.authority_orphans)} 个"
        f"（{sum(o.size_bytes for o in report.authority_orphans)} 字节）, "
        f"jobs-staging/ 孤儿 {len(report.staging_orphans)} 个"
        f"（{sum(o.size_bytes for o in report.staging_orphans)} 字节）"
    )
    if not args.apply:
        if report.count:
            print("加 --apply 执行删除（幂等可重跑）。")
        return 0

    deleted = apply_gc(client, settings.bucket, report)
    print(f"已删除 {deleted} 个孤儿对象（失败的对象下一轮扫描会重试）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
