#!/usr/bin/env python3
"""确保实例 S3 bucket 存在并配置浏览器直传所需的 CORS（幂等）。

从 scripts/init-worktree.sh 的内嵌 heredoc 抽出，供两处调用：
  - scripts/init-worktree.sh：worktree 初始化时建 per-worktree bucket；
  - scripts/dev_stack.sh up：dev-up 起本地 RustFS 后兜底建 bucket。
也可手工执行：uv run python scripts/ensure-s3-bucket.py [ENV_FILE]（默认 .env）。

退出码约定（建 bucket 失败不在这里降级，由调用方决定）：
  - 0：bucket 已就绪（含「未配置 AGENT_LEGION_S3_BUCKET」的合法跳过——
    未配置时材料 API 本就走 503 降级，不是错误）；
  - 非 0：endpoint 不可达 / 凭据错误等异常，两个调用方都降级为 warning，
    不阻断启动。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 直接以脚本路径执行（uv run python scripts/ensure-s3-bucket.py）时
# sys.path[0] 是 scripts/ 而非仓库根；把仓库根追加到末尾（PYTHONPATH 上的
# 测试探针桩仍然优先），保证 import server.app.storage 自包含。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def main() -> int:
    from dotenv import load_dotenv

    # 显式传路径：无参 load_dotenv() 走 find_dotenv 的调用栈探测，在
    # stdin heredoc（python -）模式下必抛 AssertionError（2026-08-24 实测）。
    env_file = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    load_dotenv(env_file, override=False)

    import boto3
    from botocore.exceptions import ClientError

    from server.app.storage import load_s3_settings

    settings = load_s3_settings()
    if settings is None:
        print("提示: AGENT_LEGION_S3_BUCKET 未配置，跳过建 bucket")
        return 0
    kwargs: dict[str, str] = {"region_name": settings.region}
    if settings.endpoint_url:
        kwargs["endpoint_url"] = settings.endpoint_url
    if settings.access_key:
        kwargs["aws_access_key_id"] = settings.access_key
        kwargs["aws_secret_access_key"] = settings.secret_key
    client = boto3.client("s3", **kwargs)
    try:
        client.head_bucket(Bucket=settings.bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise
        client.create_bucket(Bucket=settings.bucket)
        print(f"已创建 S3 bucket: {settings.bucket}")
    else:
        print(f"S3 bucket 已存在: {settings.bucket}")
    # 浏览器直传要求 bucket CORS 放行前端 dev server origin 的 PUT/GET，并暴露
    # ETag（前端 complete 校验用）。origin 集合 = 常用 5173/5174 + 本机
    # DEV_FRONTEND_PORT（默认 5174；端口约定见 Makefile：prod 前端 5173，
    # dev worktree 默认 5174），127.0.0.1/localhost 两种 host 形式。
    dev_port = os.environ.get("DEV_FRONTEND_PORT", "").strip() or "5174"
    wanted = {
        f"http://{host}:{port}"
        for port in ("5173", "5174", dev_port)
        for host in ("127.0.0.1", "localhost")
    }
    # 与既有规则合并而非全量覆写：bucket 可能还有手工配置的其它 origin
    # （如 prod 页面地址），读出现有 rules 后只在缺 origin 时追加一条规则。
    try:
        rules = client.get_bucket_cors(Bucket=settings.bucket)["CORSRules"]
    except ClientError:  # NoSuchCORSConfiguration：bucket 还没有 CORS 配置
        rules = []
    known = {origin for rule in rules for origin in rule.get("AllowedOrigins", [])}
    missing = sorted(wanted - known)
    if missing:
        rules.append(
            {
                "AllowedOrigins": missing,
                "AllowedMethods": ["PUT", "GET", "HEAD"],
                "AllowedHeaders": ["*"],
                "ExposeHeaders": ["ETag"],
                "MaxAgeSeconds": 3600,
            }
        )
        client.put_bucket_cors(Bucket=settings.bucket, CORSConfiguration={"CORSRules": rules})
        print(f"已配置 bucket CORS（前端 dev origin 直传）: {settings.bucket}")
    else:
        print(f"bucket CORS 已覆盖前端 dev origin: {settings.bucket}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
