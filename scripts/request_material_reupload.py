#!/usr/bin/env python3
"""材料 ETag 失配行的受控修复入口（#1279 codex r4 P1 恢复面）。

消费侧（Host 物化 / Worker 拉取）对 ready 材料的 GET 带 If-Match 核验；
对象在 complete 之后被同 URL 覆盖/丢失时得到 412——此时该 ready 行的
object_etag 已与对象脱节，同 hash 的 presign 又会 dedup 复用该行（不签
新 URL），形成「坏行永久复用」。本脚本把行翻回 uploading 并清
object_etag：此后同 hash presign 走重试路径（代次 +1、v2g 新 key、签发
新 URL），客户端重新上传即可愈合。历史 job 按 material_id 引用行，重传
完成后消费的是新对象（旧对象已毒化/丢失，412 是它的终局）；旧对象由
GC 新文法删除批回收。

用法:
    uv run python scripts/request_material_reupload.py <workspace_id> <material_id> \
        [--database-url ...]

退出码: 0 已翻牌待重传；非 0 = 行不存在/非 ready/DB 不可达等。
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

    from server.app.jobs.queries import JobQueries
    from server.app.services.job_errors import JobServiceError
    from server.app.settings import load_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace_id")
    parser.add_argument("material_id")
    parser.add_argument("--database-url", default=None, help="覆盖 AGENT_LEGION_DATABASE_URL。")
    args = parser.parse_args()

    app_settings = load_settings()
    job_db = JobQueries(
        args.database_url or app_settings.database_url, jobs_dir=app_settings.jobs_dir
    )
    try:
        job_db.request_material_reupload(args.workspace_id, args.material_id)
    except JobServiceError as exc:
        print(f"修复失败: {exc}")
        return 1
    print(
        f"材料 {args.material_id} 已翻回 uploading（object_etag 已清）。"
        "请重新上传：同 hash 再次 presign 会签发新代次 key 的 PUT URL，"
        "PUT 后 complete 即恢复 ready。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
