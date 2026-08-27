#!/usr/bin/env bash
# worktree 收尾清理：一站式移除某个已完成任务的 worktree 残留——
#   1. git worktree remove（dirty 时 git 自己拒绝，不加 --force）
#   2. 删除该 worktree 占用的本地分支（git branch -d，未合并删不掉时提示但
#      不中断后续步骤）；远端分支默认不删，仅打印提示命令，加
#      --delete-remote-branch 才执行 push --delete
#   3. 派生 Postgres 库（转调 scripts/drop-worktree-db.sh，继承其护栏）
#   4. 派生 S3 bucket（agent-legion-<worktree名>，与 init-worktree.sh 同一
#      派生规则与 env 加载）
#
# 每步 skip-if-absent，幂等可重复执行。防误删护栏：
#   - 名字校验只允许 worktree 目录名字符集，派生 bucket 一律带
#     agent-legion- 前缀，结构上碰不到共享/prod bucket；
#   - 拒绝清理脚本当前所在的 worktree 自身与 prod（生产 worktree 禁止动）；
#   - bucket 删除前列出对象数量与总大小，无 --yes 时逐个打印摘要并交互确认；
#   - endpoint 不可达/未配置 S3 时 warning 跳过（对齐 init-worktree.sh 的
#     降级语义），不影响其余步骤。
#
# 用法: scripts/clean-worktree.sh <worktree名> [--yes] [--delete-remote-branch]
set -euo pipefail

usage() {
    echo "用法: $0 <worktree名> [--yes] [--delete-remote-branch]" >&2
    exit 2
}

[[ $# -ge 1 ]] || usage
WT="$1"
shift
ASSUME_YES=""
DELETE_REMOTE=""
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES="--yes" ;;
        --delete-remote-branch) DELETE_REMOTE="--delete-remote-branch" ;;
        *) usage ;;
    esac
done

# 名字校验：只允许 worktree 目录名的合法字符集（与 drop-worktree-db.sh 同款）。
if [[ ! "$WT" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
    echo "错误: 非法 worktree 名 '$WT'" >&2
    exit 1
fi

# 脚本运行在某个 worktree 内（bare 主根上 git 操作会报 "must be run in a
# work tree"），用脚本所在仓库的当前 work tree 执行 git 命令。先记下调用方
# 的原始 cwd——下面的 cd 会离开它，而 cwd 护栏要检查的正是调用方的位置。
CALLER_CWD="$(pwd)"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 护栏：拒绝清理脚本当前所在的 worktree 自身与 prod 生产 worktree。
if [[ "$WT" == "prod" ]]; then
    echo "错误: 'prod' 是生产 worktree，禁止清理。" >&2
    exit 1
fi
if [[ "$WT" == "$(basename "$ROOT")" ]]; then
    echo "错误: 不能清理脚本当前所在的 worktree 自身（${WT}）。" >&2
    exit 1
fi

# 主仓库根 = worktree list 第一个条目；目标 worktree 一律是它的平级子目录
# .worktrees/<name>（AGENTS.md §1 的嵌套防护约定）。
MAIN="$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
TARGET="$MAIN/.worktrees/$WT"

# 护栏：拒绝清理调用方 shell 的 cwd 所在的 worktree。agent 常在目标 worktree
# 里执行本脚本做收尾——脚本自身无碍（bash 已把脚本文件读进内存），但调用
# shell 的 cwd 会随目录删除一起失效，后续任何命令都失败（cwd 报错）。要求
# 调用方先 cd 到仓库其他位置（如主仓库根）再收尾。CALLER_CWD 在上方 cd 进
# 脚本所在 worktree 之前捕获。
if [[ "$CALLER_CWD" == "$TARGET" || "$CALLER_CWD" == "$TARGET"/* ]]; then
    echo "错误: 当前 shell 的工作目录在待清理的 worktree 内（${CALLER_CWD}）。" >&2
    echo "      清理后该目录会被删除，本 shell 的后续命令将全部失效。" >&2
    echo "      请先 cd 到其他位置再执行收尾，例如:" >&2
    echo "        cd ${MAIN} && ${MAIN}/.worktrees/develop/scripts/clean-worktree.sh ${WT}" >&2
    exit 1
fi

# 1. git worktree remove（remove 前先解析该 worktree checkout 的分支，
#    供第 2 步删本地分支用）。
BRANCH=""
if [[ -d "$TARGET" ]]; then
    BRANCH="$(git worktree list --porcelain | awk -v target="$TARGET" '
        /^worktree / { wt = substr($0, 10) }
        /^branch / && wt == target { print substr($0, 8) }
    ')"
    BRANCH="${BRANCH#refs/heads/}"
    git worktree remove "$TARGET"
    echo "已移除 worktree: $TARGET"
else
    echo "worktree 不存在（跳过）: $TARGET"
fi

# 2. 本地分支：git branch -d 只允许删已合并分支，未合并报错时提示但不中断
#    后续 DB/bucket 清理。远端分支默认只打印提示命令。
if [[ -n "$BRANCH" ]]; then
    if ! git show-ref --verify --quiet "refs/heads/$BRANCH"; then
        echo "本地分支不存在（跳过）: $BRANCH"
    elif [[ "$BRANCH" == "$(git branch --show-current)" ]]; then
        echo "提示: $BRANCH 是当前 worktree 的检出分支，跳过删除" >&2
    elif git branch -d "$BRANCH"; then
        echo "已删除本地分支: $BRANCH"
    else
        echo "提示: 本地分支 $BRANCH 未合并，git branch -d 拒绝删除，已跳过" >&2
    fi
    if [[ -n "$DELETE_REMOTE" ]]; then
        git push origin --delete "$BRANCH" && echo "已删除远端分支: origin/$BRANCH"
    else
        echo "提示: 如需删除远端分支，执行 git push origin --delete $BRANCH"
        echo "      （或重跑本脚本加 --delete-remote-branch）"
    fi
else
    echo "未解析到分支（跳过本地/远端分支删除）"
fi

# 3. 派生 Postgres 库：护栏与确认交互全部继承 drop-worktree-db.sh。
"$(dirname "${BASH_SOURCE[0]}")/drop-worktree-db.sh" "$WT" $ASSUME_YES

# 4. 派生 S3 bucket：与 init-worktree.sh 同一派生规则与 env 加载。
export CLEAN_WORKTREE_WT="$WT"
export CLEAN_WORKTREE_YES="$ASSUME_YES"
if PYTHONPATH="$ROOT" UV_CACHE_DIR=.uv-cache uv run python - <<'PY'
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

# 与 init-worktree.sh 同一理由：stdin heredoc 模式下 find_dotenv 必抛
# AssertionError，必须显式传路径。
load_dotenv(Path(".env"), override=False)

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

from server.app.storage import load_s3_settings

wt = os.environ["CLEAN_WORKTREE_WT"]
assume_yes = bool(os.environ.get("CLEAN_WORKTREE_YES"))
# bucket 派生规则与 scripts/init-worktree.sh 完全一致：
# agent-legion-$(小写 | 非 [a-z0-9-] 归并为 '-')
bucket = "agent-legion-" + re.sub(r"[^a-z0-9-]", "-", wt.lower())

# 护栏：只操作派生命名的 bucket，结构上碰不到共享/prod bucket。
# 退出码约定：3=用户取消、4=护栏拒绝——这两类在调用侧原样失败，其余
# 非零（python 异常也退出 1，无法靠 1 区分）降级为 warning 跳过。
if not bucket.startswith("agent-legion-") or bucket == "agent-legion-":
    print(f"错误: 拒绝删除非派生命名的 bucket: {bucket}", file=sys.stderr)
    raise SystemExit(4)

settings = load_s3_settings()
if settings is None:
    print("提示: S3 未配置（AGENT_LEGION_S3_BUCKET 为空），跳过 bucket 清理")
    raise SystemExit(0)
kwargs = {"region_name": settings.region}
if settings.endpoint_url:
    kwargs["endpoint_url"] = settings.endpoint_url
if settings.access_key:
    kwargs["aws_access_key_id"] = settings.access_key
    kwargs["aws_secret_access_key"] = settings.secret_key
client = boto3.client("s3", **kwargs)

try:
    client.head_bucket(Bucket=bucket)
except ClientError as exc:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    if code in ("404", "NoSuchBucket", "NotFound"):
        print(f"S3 bucket 不存在（跳过）: {bucket}")
        raise SystemExit(0)
    raise

# 列出对象数量与总大小（分页），供确认与删除。
objects = []
paginator = client.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=bucket):
    objects.extend(page.get("Contents", []))
total_size = sum(obj["Size"] for obj in objects)
print(f"待删除 bucket: {bucket} (对象数={len(objects)}, 总大小={total_size} 字节)")

if not assume_yes:
    reply = input(f"确认删除 bucket {bucket}（含全部 {len(objects)} 个对象）？输入 worktree 名 '{wt}' 确认: ")
    if reply != wt:
        print("已取消。", file=sys.stderr)
        raise SystemExit(3)

# 非空 bucket 需先清空对象再 delete_bucket（不可逆操作）。
for start in range(0, len(objects), 1000):
    batch = objects[start : start + 1000]
    client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": obj["Key"]} for obj in batch]},
    )
client.delete_bucket(Bucket=bucket)
print(f"已删除 S3 bucket: {bucket}（含 {len(objects)} 个对象）")
PY
then
    :
else
    rc=$?
    # 3=用户取消、4=护栏拒绝：原样失败；其余（endpoint 不可达、boto3 缺失、
    # 凭据错误等 python 异常）降级为 warning 跳过，对齐 init 的降级语义。
    if [[ "$rc" -eq 3 || "$rc" -eq 4 ]]; then
        exit 1
    fi
    echo "提示: S3 endpoint 不可达或清理失败（exit=$rc），跳过 bucket 清理。" >&2
    echo "      待共享 RustFS 可达后可重跑本脚本补齐。" >&2
fi

echo "完成: worktree '$WT' 收尾清理结束。"
