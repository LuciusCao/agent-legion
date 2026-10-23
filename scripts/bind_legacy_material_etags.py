#!/usr/bin/env python3
"""存量无 hash 材料的 ETag 身份绑定（#1316）。dry-run 默认。

新 Host 每次启动都会在后台自动跑同一逻辑（server/app/services/
material_legacy_etag_binding.py），本工具用于：启动线程因存储故障放弃后的
补跑、DB 恢复后不想重启 Host 时的补跑、以及运维核对还剩哪些行没绑上。

- 候选：status='ready' 且 content_hash='' 且 object_etag='' 的行（v98 前完成）；
- 逐行 HEAD：对象存在、size 与行一致、存储返回 ETag → CAS 绑定（同条 UPDATE
  把 upload_generation +1，未绑窗口内写入的缓存地址孤儿化）；否则只报告，
  不改行（该行保持 size-only）；
- 本工具**不触发 schema 迁移**：库未被新 Host 迁移到当前版本时拒绝运行
  （先升级并启动 Host——迁移只能由停机升级后的新 Host 落地）。

退出码: 0 无剩余未绑定行（或 dry-run 无问题行）；1 存在无法绑定的行或前置条件不满足。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def schema_at_head(database_url: str) -> bool:
    """库是否已被迁移到本代码的 SCHEMA_VERSION（只读探测，不迁移）。"""
    from server.app.db.schema import SCHEMA_VERSION
    from server.app.db.transaction import read_connection

    with read_connection(database_url) as conn:
        exists = conn.execute("select to_regclass('schema_migrations') as t").fetchone()
        if exists is None or exists["t"] is None:
            return False
        row = conn.execute("select max(version) as v from schema_migrations").fetchone()
    return row is not None and row["v"] is not None and int(row["v"]) >= SCHEMA_VERSION


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    # 显式传路径：无参 load_dotenv() 走调用栈探测，子进程场景不可靠。
    load_dotenv(Path(ROOT / ".env"), override=False)

    from server.app.services.material_legacy_etag_binding import bind_legacy_material_etags
    from server.app.settings import load_settings
    from server.app.storage import build_s3_storage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行绑定（默认 dry-run 只报告）。")
    parser.add_argument("--database-url", default=None, help="覆盖 AGENT_LEGION_DATABASE_URL。")
    args = parser.parse_args(argv)

    storage = build_s3_storage()
    if storage is None:
        print("提示: AGENT_LEGION_S3_BUCKET 未配置，无法绑定（无对象存储）")
        return 1
    database_url = args.database_url or load_settings().database_url
    if not schema_at_head(database_url):
        print("拒绝运行: 数据库尚未迁移到当前 schema 版本——先完成停机升级并启动新 Host")
        return 1
    report = bind_legacy_material_etags(database_url, storage, apply=args.apply)

    label = "apply" if args.apply else "dry-run"
    print(f"{label}: 绑定 {len(report.bound)} 行")
    problems = [
        ("对象缺失", report.missing_object),
        ("size 与行不符", report.size_mismatch),
        ("存储未返回 ETag", report.empty_etag),
        ("HEAD 失败（可重跑）", report.head_errors),
        ("行被并发修改（CAS 让位）", report.cas_lost),
    ]
    for title, ids in problems:
        if ids:
            print(f"{title} {len(ids)} 行（未改动，保持 size-only）：")
            for material_id in ids:
                print(f"  {material_id}")
    if report.unresolved:
        return 1
    if not args.apply and report.bound:
        print("加 --apply 执行绑定（幂等可重跑）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
