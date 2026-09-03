#!/usr/bin/env python3
"""报告孤儿 S3 bucket：agent-legion-* 派生 bucket 中没有对应 worktree 的（#340）。

只报告，不删除——每行给出建议的收尾命令（scripts/clean-worktree.sh，全流程
skip-if-absent 幂等：worktree/分支/库已不存在时逐步跳过，只剩 bucket 清理）。

派生规则与 scripts/init-worktree.sh / clean-worktree.sh 完全一致：
  agent-legion-$(小写 worktree 名 | 非 [a-z0-9-] 归并为 '-')
护栏：只看 agent-legion- 前缀且不等于裸前缀的 bucket，且排除当前 .env 配置的
主 bucket——报告永远不触及共享/生产 bucket。

用法: uv run python scripts/report-orphan-s3-buckets.py [ENV_FILE]
退出码: 0 正常（含发现 0 个孤儿）；非 0 = endpoint 不可达/凭据错误等。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# 直接以脚本路径执行时 sys.path[0] 是 scripts/ 而非仓库根；把仓库根追加到
# 末尾（PYTHONPATH 上的测试探针桩仍然优先），保证 import 自包含。
# （与 scripts/ensure-s3-bucket.py 同款处理。）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

DERIVED_PREFIX = "agent-legion-"


def derived_bucket(worktree_name: str) -> str:
    """与 init-worktree.sh / clean-worktree.sh 同一派生规则。"""
    return DERIVED_PREFIX + re.sub(r"[^a-z0-9-]", "-", worktree_name.lower())


def existing_worktree_names() -> set[str]:
    """现存 worktree 目录名集合（含 Tower 嵌套 wt-*，它们同样派生 bucket）。"""
    out = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    names: set[str] = set()
    for line in out.splitlines():
        if line.startswith("worktree "):
            names.add(Path(line[len("worktree ") :]).name)
    return names


def main() -> int:
    from dotenv import load_dotenv

    # 显式传路径：无参 load_dotenv() 走调用栈探测，在 heredoc/子进程场景不可靠。
    env_file = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    load_dotenv(env_file, override=False)

    import boto3

    from server.app.storage import load_s3_settings

    settings = load_s3_settings()
    if settings is None:
        print("提示: AGENT_LEGION_S3_BUCKET 未配置，跳过孤儿 bucket 报告")
        return 0
    kwargs: dict[str, str] = {"region_name": settings.region}
    if settings.endpoint_url:
        kwargs["endpoint_url"] = settings.endpoint_url
    if settings.access_key:
        kwargs["aws_access_key_id"] = settings.access_key
        kwargs["aws_secret_access_key"] = settings.secret_key
    client = boto3.client("s3", **kwargs)

    expected = {derived_bucket(name) for name in existing_worktree_names()}
    candidates = [
        b["Name"]
        for b in client.list_buckets().get("Buckets", [])
        # 护栏：只统计派生命名空间（同 clean-worktree.sh：前缀 + 非裸前缀），
        # 排除当前主 bucket——两者即使恰好可派生也绝不列入报告。
        if b["Name"].startswith(DERIVED_PREFIX)
        and b["Name"] != DERIVED_PREFIX
        and b["Name"] != settings.bucket
    ]
    orphans = [name for name in candidates if name not in expected]

    if not orphans:
        print(f"孤儿派生 bucket: 0 个（共 {len(candidates)} 个派生 bucket 均有对应 worktree）")
        return 0

    print(f"孤儿派生 bucket: {len(orphans)} 个（对应 worktree 已不存在）\n")
    for name in sorted(orphans):
        count = 0
        total_bytes = 0
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=name):
            for obj in page.get("Contents", []):
                count += 1
                total_bytes += int(obj.get("Size", 0))
        wt_name = name[len(DERIVED_PREFIX) :]
        print(f"  {name}: {count} 个对象, {total_bytes} 字节")
        print(f"    收尾: scripts/clean-worktree.sh {wt_name} --yes")
    print(
        "\nclean-worktree.sh 全流程 skip-if-absent 幂等：worktree/分支/库已不存在时"
        "逐步跳过，bucket 清理前仍会列出对象摘要。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
