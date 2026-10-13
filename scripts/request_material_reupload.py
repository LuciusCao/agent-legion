#!/usr/bin/env python3
"""材料 ETag 失配行的受控修复入口（#1279 codex r4/r5 恢复面）。

消费侧（Host 物化 / Worker 拉取）对 ready 材料的 GET 带 If-Match 核验；
对象在 complete 之后被同 URL 覆盖/丢失时得到 412——此时该 ready 行的
object_etag 已与对象脱节，同 hash 的 presign 又会 dedup 复用该行（不签
新 URL），无 hash 行更没有 presign 找回旧行的回路——形成「坏行永久
复用」。本脚本把原行翻回 uploading（代次 +1、v2g 新 key、清
object_etag）并为新 key 签发受代次保护的 presigned PUT URL：用它 PUT
重传后 complete（携带输出里的代次）即恢复 ready，历史 job/bundle 对原
material_id 的引用随之愈合（重传后消费新对象；旧对象已毒化/丢失，412
是它的终局）。旧对象由 GC 新文法删除批回收。

用法:
    uv run python scripts/request_material_reupload.py <workspace_id> <material_id> \
        [--database-url ...]

输出: 轮换后的代次与一次性重传 PUT URL（有效期 1 小时）。
退出码: 0 已翻牌并签发 URL；非 0 = 行不存在/expired/存储未配置/DB 不可达等。
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

    from server.app.services.job_errors import JobServiceError
    from server.app.services.materials import MaterialsService
    from server.app.settings import load_settings
    from server.app.storage import build_s3_storage

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace_id")
    parser.add_argument("material_id")
    parser.add_argument("--database-url", default=None, help="覆盖 AGENT_LEGION_DATABASE_URL。")
    args = parser.parse_args()

    storage = build_s3_storage()
    if storage is None:
        print("修复失败: 对象存储未配置（AGENT_LEGION_S3_BUCKET unset）")
        return 1
    app_settings = load_settings()
    service = MaterialsService(args.database_url or app_settings.database_url, storage)
    try:
        outcome = service.request_material_reupload(args.workspace_id, args.material_id)
    except JobServiceError as exc:
        print(f"修复失败: {exc}")
        return 1
    print(
        f"材料 {args.material_id} 已翻回 uploading（代次 "
        f"{outcome['upload_generation']}、key 已轮换、object_etag 已清）。"
        "请用下面的一次性 PUT URL 重传（1 小时有效），随后 complete 携带代次：\n"
        f"  PUT {outcome['upload_url']}\n"
        f"  complete: expected_upload_generation={outcome['upload_generation']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
