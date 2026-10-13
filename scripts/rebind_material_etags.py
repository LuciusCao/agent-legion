#!/usr/bin/env python3
"""S3 快照恢复后的材料 ETag 重绑（#1279 codex r4 P1 恢复面）。dry-run 默认。

按受支持的恢复流程（docs/backup-restore-runbook.md）清空 bucket 并经
`aws s3 cp --recursive` 重传对象后，大行会被 AWS CLI 分段上传——即使字节
与 sha256 完全相同，ETag 也与备份前不同。materials.object_etag 仍是旧值，
消费侧（Host If-Match / Worker If-Match）将全线 412。本工具必须在恢复后、
启动调度前运行：

- 扫描全部记录了 object_etag 的材料行（bundle 成员行同在 materials 表，
  天然覆盖），HEAD 比对当前对象；
- ETag 一致：不动；
- ETag 不一致：先核验恢复字节（size 恒验 + content_hash 非空时流式
  sha256）——通过才在事务内以 CAS（行的 object_etag 仍为扫描值）重绑为
  恢复后对象的 HEAD ETag；字节不符或对象缺失则 fail-closed 只报告；
- dry-run（默认）只报告将重绑的行；--apply 执行重绑。

退出码: 0 全部一致/已重绑；1 存在无法重绑的行（缺对象/字节不符/CAS 竞争）。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


# 注入面：重绑所需的 DB 最小形状（JobQueries 门面方法，测试中换假实现）。
class RebindQueries(Protocol):
    def materials_with_object_etag(self) -> list[dict[str, Any]]: ...

    def rebind_material_etag(
        self, material_id: str, *, expected_etag: str, new_etag: str, storage_key: str
    ) -> bool: ...


@dataclass
class RebindReport:
    """重绑结果：一致 / 已（将）重绑 / 无法重绑（fail-closed）三类。"""

    matched: int = 0
    rebound: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def rebind_material_etags(storage: Any, queries: RebindQueries, *, apply: bool) -> RebindReport:
    """逐行 HEAD 比对并按字节核验重绑；核心判定与 CLI 分离以便假存储直测。"""
    from server.app.services.material_key_validation import verify_stored_object

    report = RebindReport()
    for row in queries.materials_with_object_etag():
        material_id = str(row["id"])
        recorded = str(row["object_etag"])
        head = storage.head_object(str(row["storage_key"]))
        if head is not None and head.etag == recorded:
            report.matched += 1
            continue
        # ETag 不一致（或对象缺失）：先核验恢复字节，通过才允许重绑。
        failure, fresh_etag = verify_stored_object(storage, row)
        if failure is not None or not fresh_etag:
            report.unresolved.append(f"{material_id}: {failure or 'no etag on object'}")
            continue
        if not apply:
            report.rebound.append(material_id)
            continue
        if queries.rebind_material_etag(
            material_id,
            expected_etag=recorded,
            new_etag=fresh_etag,
            storage_key=str(row["storage_key"]),
        ):
            report.rebound.append(material_id)
        else:
            report.unresolved.append(f"{material_id}: row changed concurrently (CAS lost)")
    return report


def main() -> int:
    from dotenv import load_dotenv

    # 显式传路径：无参 load_dotenv() 走调用栈探测，子进程场景不可靠。
    load_dotenv(Path(ROOT / ".env"), override=False)

    from server.app.jobs.queries import JobQueries
    from server.app.settings import load_settings
    from server.app.storage import build_s3_storage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行重绑（默认 dry-run 只报告）。")
    parser.add_argument("--database-url", default=None, help="覆盖 AGENT_LEGION_DATABASE_URL。")
    args = parser.parse_args()

    storage = build_s3_storage()
    if storage is None:
        print("提示: AGENT_LEGION_S3_BUCKET 未配置，无法重绑（无对象存储）")
        return 1
    app_settings = load_settings()
    job_db = JobQueries(
        args.database_url or app_settings.database_url, jobs_dir=app_settings.jobs_dir
    )
    report = rebind_material_etags(storage, job_db, apply=args.apply)

    label = "apply" if args.apply else "dry-run"
    print(f"{label}: ETag 一致 {report.matched} 行, 重绑 {len(report.rebound)} 行")
    for material_id in report.rebound:
        print(f"  重绑: {material_id}")
    if report.unresolved:
        print(f"无法重绑 {len(report.unresolved)} 行（fail-closed，未改动）：")
        for detail in report.unresolved:
            print(f"  {detail}")
        return 1
    if not args.apply and report.rebound:
        print("加 --apply 执行重绑（幂等可重跑）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
