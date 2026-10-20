#!/usr/bin/env bash
# worktree 收尾清理：一站式移除某个已完成任务的 worktree 残留——
#   0. 删除计划（只读）：记录目标 worktree 身份快照、列出派生库、核对
#      bucket，需要时一次性交互确认；之后取派生名锁（#1084）一直持有到
#      第 4 步结束
#   1. git worktree remove（dirty 时 git 自己拒绝，不加 --force）
#   2. 删除该 worktree 占用的本地分支（git branch -d，未合并删不掉时提示但
#      不中断后续步骤）；远端分支默认不删，仅打印提示命令，加
#      --delete-remote-branch 才执行 push --delete
#   3. 派生 Postgres 库（与 scripts/drop-worktree-db.sh 共用
#      worktree-names-lib.sh 的探测/删除函数，在本进程内同步执行）
#   4. 派生 S3 bucket（agent-legion-<worktree名>，与 init-worktree.sh 同一
#      派生规则与 env 加载）；bucket 删除后（或已不存在时）经 SeaweedFS
#      master 只读核对同名 collection，残留卷只告警并给出手动回收命令
#      （scripts/seaweedfs_collection.py，#824；非 seaweedfs 后端自动跳过）
#
# 每步 skip-if-absent，幂等可重复执行。防误删护栏：
#   - 名字校验只允许 worktree 目录名字符集，派生 bucket 一律带
#     agent-legion- 前缀，结构上碰不到共享/prod bucket；
#   - 拒绝清理脚本当前所在的 worktree 自身与 prod（生产 worktree 禁止动）；
#   - --delete-remote-branch 拒绝删除长期分支 main/master/prod 与
#     发布线 release/*（与 pre-push 的 protected 分支口径对齐，#930）；
#   - 删除前列出派生库 owner/大小与 bucket 对象数量/总大小，无 --yes 时
#     交互确认（在取锁之前，持锁不等用户输入）；
#   - 取锁后、git worktree remove 前做最终校验且校验与 remove 之间不再
#     有任何步骤（#1084）：身份快照（HEAD/分支/.git 文件指纹）+ porcelain
#     条目（path/HEAD/branch）逐字段比对——确认/等锁期间目标被移除并以
#     同名同路径重建（哪怕已跑完 init-worktree.sh，裸 git add 不入锁协议）
#     时拒绝——否则删除会落到新实例的 worktree/分支/库/bucket 上；
#   - 派生库/bucket 的存在性以取锁后的重探测为准：锁前 probe 只用于确认
#     展示与计划集合，等锁期间 init 补建的资源不会被陈旧的「不存在」结果
#     漏删（#1084）；锁内集合比确认计划扩大（计划外资源）时交互模式中止、
#     --yes 模式警告后继续——不删除用户从未见过、未确认的资源（#1084）；
#   - 删库/删 bucket 前持锁重查：同名 worktree 被重建后又注册着也拒绝；
#   - 全部建/删步骤在持锁进程内同步执行（R5 熔断收窄）：锁内没有嵌套或
#     后台执行者，holder 的 pid 即执行者本身；本进程被 SIGKILL 的残余
#     窗口只有「在途的那一条命令执行完」，不会有孤儿进程继续推进删除。
#     唯一的多步前台子进程是 bucket delete 的 uv/python——它持有删除
#     租约（holder 文件 + owner pid），每个破坏性步骤前校验，失效即自绝
#     （#1084）；
#   - bucket 探测失败（endpoint/鉴权/python 故障，exit 非 0/4/10）fail-closed
#     中止：探测失败 ≠ 不存在，状态未知时不确认、不推进、不盲删（#1084）；
#     未配置 S3（确定性状态）仍提示跳过；delete 执行本身失败则 warning 并
#     提示重跑补齐（删除决策基于锁内成功探测）。
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
# 允许 '.'（release-0.7.8 这类集成分支 worktree，#587）：首字符必须是字母
# 数字且不允许 '/'，'.'/'..' 与任何路径分隔形态结构上进不来；派生 DB/bucket
# 名把 '.' 归并为 '_'/'-'，仍落在派生前缀护栏内。
if [[ ! "$WT" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]; then
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
MAIN="$(git worktree list --porcelain | awk '/^worktree /&&!seen{print $2; seen=1}')"
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

# 护栏（#950）：派生名映射不是单射，另一个仍存在的 worktree 派生出同名
# 库/bucket 时整体拒绝——在第 1 步移除 worktree 之前判定，不留半清理状态。
# 这里是不持锁的预检（尽早拒绝），权威判定在下方取锁之后重做。
# shellcheck source=worktree-names-lib.sh
source "$ROOT/scripts/worktree-names-lib.sh"
worktree_require_unique_derived_names "$ROOT" "$WT" "清理（删除派生库/bucket）" || exit 1

# 目标身份快照（#1084）：计划阶段记录，取锁后、git worktree remove 前重验。
# 确认/等锁期间另一流程可能移除原 worktree 并以同名同路径重建（甚至跑完
# init-worktree.sh）——撞名检查不把同名条目算冲突，识别不了这种替换，没有
# 身份重验的话删除会落到新实例上。
# stat 方言（#1084）：BSD（macOS）的 -f 指定输出格式，GNU（Linux）的 -f 是
# 「查文件系统」标志——同一调用在 GNU 上会往 stdout 打印动态文件系统计数
# （可用块/inode 数随锁 holder 文件创建而变化）再报错，靠错误回退拼接会
# 把动态计数混进指纹，把未变化的实例误判为被重建。按 stat --version 探测
# 一次方言后分流，不做错误回退。
_STAT_DIALECT=""
_stat_dialect() {
    if [[ -z "$_STAT_DIALECT" ]]; then
        if stat --version >/dev/null 2>&1; then
            _STAT_DIALECT=gnu
        else
            _STAT_DIALECT=bsd
        fi
    fi
    printf '%s' "$_STAT_DIALECT"
}
_identity_stat() {
    # $1: 路径, $2: BSD stat 格式, $3: GNU stat 格式。GNU 的 %W（birthtime）
    # 在不支持的文件系统上为 0/-，inode+ctime 已足够区分重建。
    if [[ "$(_stat_dialect)" == "gnu" ]]; then
        stat -c "$3" "$1" 2>/dev/null || echo "no-stat"
    else
        stat -f "$2" "$1" 2>/dev/null || echo "no-stat"
    fi
}
target_identity() {
    if [[ ! -d "$TARGET" ]]; then
        echo "absent"
        return 0
    fi
    local head branch gitinfo dir_fp file_fp
    head="$(git -C "$TARGET" rev-parse HEAD 2>/dev/null)" || head="unresolved"
    branch="$(git -C "$TARGET" symbolic-ref --short -q HEAD 2>/dev/null)" || branch="detached"
    gitinfo="$(head -c 300 "$TARGET/.git" 2>/dev/null)" || gitinfo="no-gitfile"
    # 目录只用 device:inode:birthtime——init 在目录内创建文件会改变目录的
    # ctime/mtime，等锁期间并发 init 是合法场景，不能据此误判替换；.git
    # 文件 init 从不触碰，可以用完整指纹（含 ctime）。remove+add 重建后
    # 目录与 .git 文件必然重写，inode/birthtime/ctime 至少其一变化。
    dir_fp="$(_identity_stat "$TARGET" '%d:%i:%B' '%d:%i:%W')"
    file_fp="$(_identity_stat "$TARGET/.git" '%d:%i:%c:%B' '%d:%i:%Z:%W')"
    printf 'present\n%s\n%s\n%s\n%s\n%s\n' "$head" "$branch" "$gitinfo" "$dir_fp" "$file_fp"
}
PLANNED_IDENTITY="$(target_identity)"
# 目标在 porcelain 里的条目（HEAD/branch/detached 行）：与身份快照一起，
# 在 remove 前的最终校验里逐字段比对（#1084：裸 git worktree add 不入锁
# 协议，条目变化即状态已变）。
worktree_porcelain_entry() {
    git worktree list --porcelain | awk -v target="$TARGET" '
        /^worktree / { wt = substr($0, 10); next }
        wt == target && /^(HEAD |branch |detached$)/ { print }
    '
}
PLANNED_ENTRY="$(worktree_porcelain_entry)"

# 派生 S3 bucket 的两阶段 python（与 init-worktree.sh 同一派生规则与 env
# 加载，#1084）：probe 只读核对并打印摘要，delete 持锁后重新 head/list 再
# 删除。确认在 bash 侧做——旧实现在 heredoc python 里 input()，stdin 已是
# EOF 必抛 EOFError，无 --yes 时 bucket 步骤只会降级为 warning 跳过。
# 退出码约定（两阶段通用）：0=无需/已完成、10=probe 发现 bucket 待删、
# 4=护栏拒绝；其余（python 异常也退出 1）= 失败——probe 阶段 fail-closed
# 中止，delete 阶段降级为 warning 提示重跑（见调用侧）。
export CLEAN_WORKTREE_WT="$WT"
run_bucket_phase() {
    # 删除租约（#1084 熔断重估 R2）：delete 阶段的实际执行者是 uv/python
    # 前台子进程——本 shell 被 SIGKILL 时它会成孤儿继续跑多批删除，而锁
    # holder 只记本进程 pid，竞争者回收锁后同名 init 会建/用同名 bucket。
    # 因此 delete 阶段把 holder 文件路径（租约）与 holder pid 经 env 传给
    # python，python 在每个破坏性步骤前校验两者仍有效，失效即放弃剩余
    # 删除。回收方保持 pid 判活不变——孤儿自绝后无需等它。probe 只读，
    # 不传租约。
    local phase="$1" lease_file="" owner_pid=""
    if [[ "$phase" == "delete" ]]; then
        if ! lease_file="$(worktree_name_lock_lease_file)"; then
            echo "错误: 未持有派生名锁（无删除租约），拒绝 bucket 删除（#1084）。" >&2
            return 4
        fi
        owner_pid="$$"
    fi
    CLEAN_WORKTREE_PHASE="$phase" PYTHONPATH="$ROOT" UV_CACHE_DIR=.uv-cache \
    AGENT_LEGION_WT_CLEAN_LEASE="$lease_file" AGENT_LEGION_WT_CLEAN_OWNER_PID="$owner_pid" \
    uv run --frozen python - <<'PY'
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

from scripts.seaweedfs_collection import (
    CollectionGuardError,
    leftover_volume_ids,
    manual_reclaim_command,
    resolve_master_url,
)
from server.app.storage import load_s3_settings

wt = os.environ["CLEAN_WORKTREE_WT"]
phase = os.environ["CLEAN_WORKTREE_PHASE"]
# bucket 派生规则与 scripts/init-worktree.sh 完全一致：
# agent-legion-$(小写 | 非 [a-z0-9-] 归并为 '-')
bucket = "agent-legion-" + re.sub(r"[^a-z0-9-]", "-", wt.lower())

# 护栏：只操作派生命名的 bucket，结构上碰不到共享/prod bucket。
# 退出码约定见调用侧（4=护栏拒绝，原样失败）。
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


def report_seaweedfs_leftovers() -> None:
    """bucket 已确认不存在后只读核对同名 collection 的残留卷（#824）。

    DeleteBucket 正常会连带删 collection；master 侧失败/超时则卷仍占槽位。
    这里只告警并给出手动回收命令、从不自动删除：bucket 不存在由 S3 判定，
    删除却打在 master 上，两步之间同名 worktree 可能被重建复用，目标身份
    无法在本脚本内钉死。master 不可达只提示，均不影响退出码。
    """
    master = resolve_master_url(settings.endpoint_url, os.environ)
    if master is None:
        return
    try:
        leftover = leftover_volume_ids(master, bucket)
    except CollectionGuardError as exc:
        print(f"提示: {exc}，跳过 collection 残留卷核查")
        return
    except OSError as exc:  # URLError/超时/连接拒绝都是 OSError 子类
        print(f"提示: SeaweedFS master {master} 不可达（{exc}），跳过 collection 残留卷核查")
        return
    if not leftover:
        print(f"SeaweedFS collection {bucket} 无残留卷")
        return
    print(
        f"警告: bucket {bucket} 已删除，但 SeaweedFS collection 仍有卷残留: {list(leftover)}",
        file=sys.stderr,
    )
    print(
        f"      确认没有同名 worktree 正在复用该 bucket 后手动回收: {manual_reclaim_command(master, bucket)}",
        file=sys.stderr,
    )


try:
    client.head_bucket(Bucket=bucket)
except ClientError as exc:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    if code in ("404", "NoSuchBucket", "NotFound"):
        print(f"S3 bucket 不存在（跳过）: {bucket}")
        report_seaweedfs_leftovers()
        raise SystemExit(0)
    raise

# 列出对象数量与总大小（分页）：probe 阶段供确认，delete 阶段持锁后重新
# 列出再删（两阶段之间对象可能变化）。
objects = []
paginator = client.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=bucket):
    objects.extend(page.get("Contents", []))
total_size = sum(obj["Size"] for obj in objects)
if phase == "probe":
    print(f"待删除 bucket: {bucket} (对象数={len(objects)}, 总大小={total_size} 字节)")
    raise SystemExit(10)
if phase != "delete":
    print(f"错误: 未知阶段 {phase!r}", file=sys.stderr)
    raise SystemExit(4)

# 逐批租约校验（#1084 熔断重估 R2）：本进程是 clean 的前台子进程
#（uv → python）。clean 被 SIGKILL 后本进程成孤儿仍能继续删，但锁 holder
# 只记 clean 的 pid，会被竞争者回收、同名 init 随即建/用同名 bucket。
# 每个破坏性步骤前校验：①持锁 shell（owner pid）仍存活；②holder 文件
#（租约）仍在。任一失败 = 锁已易主或父死，继续删就是删别人的资源——放弃
# 剩余删除并报错退出。owner 为 zombie 时 kill(pid, 0) 仍成功，与回收方的
# pid 判活口径一致（该活性残余见 #1251）。
lease_file = os.environ.get("AGENT_LEGION_WT_CLEAN_LEASE", "")
_owner_pid_raw = os.environ.get("AGENT_LEGION_WT_CLEAN_OWNER_PID", "")
owner_pid = int(_owner_pid_raw) if _owner_pid_raw.isdigit() else 0
deleted_count = 0


def lease_check() -> None:
    if not lease_file or owner_pid <= 0:
        print("错误: 缺少删除租约（holder 文件/owner pid 未传入），拒绝删除（#1084）", file=sys.stderr)
        raise SystemExit(4)
    try:
        os.kill(owner_pid, 0)
    except OSError:
        print(
            f"错误: 持锁进程（pid={owner_pid}）已退出，租约失效——放弃剩余删除"
            f"（已删 {deleted_count} 个对象，其余对象与 bucket 保留，#1084）",
            file=sys.stderr,
        )
        raise SystemExit(4)
    if not os.path.exists(lease_file):
        # 纵深防御分支：holder 文件消失仅由 owner release 或回收方删死 pid
        # 触发，都蕴含 owner 已死/在退出——上方 pid 分支必先命中，本分支在
        # 现实流程中几乎不可达，测试不单独覆盖。
        print(
            f"错误: 派生名锁 holder 已不在（{lease_file}），锁已易主——放弃剩余删除"
            f"（已删 {deleted_count} 个对象，其余对象与 bucket 保留，#1084）",
            file=sys.stderr,
        )
        raise SystemExit(4)


# 非空 bucket 需先清空对象再 delete_bucket（不可逆操作）。
for start in range(0, len(objects), 1000):
    batch = objects[start : start + 1000]
    lease_check()
    client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": obj["Key"]} for obj in batch]},
    )
    deleted_count += len(batch)
lease_check()
client.delete_bucket(Bucket=bucket)
print(f"已删除 S3 bucket: {bucket}（含 {len(objects)} 个对象）")
report_seaweedfs_leftovers()
PY
}

# 4=护栏拒绝：原样失败。探测（probe）失败 fail-closed 由调用侧处理；此处
# 的降级只覆盖 delete 阶段执行失败（endpoint 抖动、boto3 异常等）——删除
# 决策基于锁内成功探测，残留可重跑补齐。
bucket_phase_degraded() {
    echo "提示: S3 endpoint 不可达或清理失败（exit=$1），跳过 bucket 清理。" >&2
    echo "      待共享对象存储（SeaweedFS/RustFS）可达后可重跑本脚本补齐。" >&2
}

# 0. 删除计划（只读、不持锁）：列出派生库、核对 bucket，需要删除时一次性
#    交互确认（--yes 跳过）。确认必须在取锁之前——持锁等用户输入会卡住同机
#    所有 init（#1084）。注意：这里的 probe 结果只用于确认展示与计划集合
#    （PLANNED_DBS/PLANNED_BUCKET），删除决策在取锁后以锁内重探测为准（等锁
#    期间 init 可能补建资源）；锁内集合比计划扩大时交互模式中止（见下方
#    取锁后的比对，#1084）。
worktree_db_prepare
db_rc=0
worktree_db_collect "$WT" print
if [[ "$_WT_DB_FOUND" -eq 0 ]]; then
    echo "没有需要删除的库。"
else
    db_rc=10
fi
PLANNED_DBS="$_WT_DB_LIST"
bucket_rc=0
run_bucket_phase probe || bucket_rc=$?
if [[ "$bucket_rc" -eq 4 ]]; then
    exit 1
elif [[ "$bucket_rc" -ne 0 && "$bucket_rc" -ne 10 ]]; then
    # 探测失败（endpoint/鉴权/python 故障）≠ bucket 不存在（#1084）：状态
    # 未知时不确认、不推进——否则服务在锁内重探测前恢复，会以免确认模式
    # 删除从未展示过的 bucket。
    echo "错误: 派生 bucket 探测失败（exit=${bucket_rc}），无法确认其状态，中止清理（#1084）。" >&2
    echo "      请在 S3 endpoint 恢复后重跑本脚本。" >&2
    exit 1
fi
PLANNED_BUCKET=""
if [[ "$bucket_rc" -eq 10 ]]; then
    PLANNED_BUCKET=1
fi
if [[ -z "$ASSUME_YES" && ( "$db_rc" -eq 10 || "$bucket_rc" -eq 10 ) ]]; then
    reply=""
    read -r -p "确认清理 worktree '$WT' 并删除以上派生库/bucket（含全部对象）？输入 worktree 名 '$WT' 确认: " reply || true
    if [[ "$reply" != "$WT" ]]; then
        echo "已取消。" >&2
        exit 1
    fi
fi

# 取派生名锁并持有到派生库与 bucket 都处理完（#1084）：从移除 worktree 起
# 到最后一个删除之间，派生名相同（含同名重建）的 worktree 即使已经
# `git worktree add`，其 init 也只能等本次删完再建库/建 bucket，不会复用
# 半清理的资源。锁在脚本退出（含报错/信号）时由 trap 释放；全部删除步骤
# 在本进程内同步执行（R5：不再转调 drop、不再后台执行 bucket delete）。
# 取锁后重做撞名检查（目标此刻仍合法注册）。
worktree_name_lock_acquire "$ROOT" "清理 worktree ${WT}" || exit 1
worktree_require_unique_derived_names "$ROOT" "$WT" "清理（删除派生库/bucket）" || exit 1

# 锁内重探测（#1084）：锁前 probe 只用于确认展示与计划集合；等锁期间 init
# 可能补建资源，删除集合以锁内实际存在的为准。
worktree_db_collect "$WT"
bucket_rc=0
run_bucket_phase probe || bucket_rc=$?
if [[ "$bucket_rc" -eq 4 ]]; then
    exit 1
elif [[ "$bucket_rc" -ne 0 && "$bucket_rc" -ne 10 ]]; then
    echo "错误: 锁内派生 bucket 重探测失败（exit=${bucket_rc}），bucket 状态未知，中止（#1084）。" >&2
    echo "      worktree/派生库可能已删除；S3 endpoint 恢复后重跑本脚本补齐 bucket 清理。" >&2
    exit 1
fi

# 计划外资源比对（#1084 P1-b）：锁内集合比锁前确认计划扩大（出现计划外
# 库/bucket）时——交互模式中止（这些资源用户从未见过、未确认），--yes
# 模式打印警告继续（自动化语义）。
EXTRA_DBS=""
for db in $_WT_DB_LIST; do
    case "${PLANNED_DBS} " in
        *" ${db} "*) ;;
        *) EXTRA_DBS="${EXTRA_DBS} ${db}" ;;
    esac
done
EXTRA_BUCKET=""
if [[ "$bucket_rc" -eq 10 && -z "$PLANNED_BUCKET" ]]; then
    EXTRA_BUCKET=1
fi
if [[ -n "$EXTRA_DBS" || -n "$EXTRA_BUCKET" ]]; then
    extra_msg="锁内重探测发现确认计划外的资源（等锁期间同名实例可能刚初始化）："
    [[ -n "$EXTRA_DBS" ]] && extra_msg="${extra_msg} 库[${EXTRA_DBS# }]"
    [[ -n "$EXTRA_BUCKET" ]] && extra_msg="${extra_msg} bucket"
    if [[ -n "$ASSUME_YES" ]]; then
        echo "提示: ${extra_msg}按 --yes 语义一并清理。" >&2
    else
        echo "错误: ${extra_msg}" >&2
        echo "      这些资源从未展示、未经确认，已中止。请重跑本脚本查看并确认最新删除计划。" >&2
        exit 1
    fi
fi

# 1. git worktree remove。分支解析移到最终校验之前；最终校验（身份指纹 +
#    porcelain 条目逐字段比对）与 remove 之间不再有任何步骤（#1084 P1-a：
#    裸 git worktree add 不入锁协议，校验到 remove 的系统调用间微秒窗是
#    声明的固有竞态）。校验失败 = 状态已变化，中止不动任何资源。
BRANCH=""
if [[ -d "$TARGET" ]]; then
    BRANCH="$(git worktree list --porcelain | awk -v target="$TARGET" '
        /^worktree / { wt = substr($0, 10) }
        /^branch / && wt == target { print substr($0, 8) }
    ')"
    BRANCH="${BRANCH#refs/heads/}"
fi
if [[ "$(target_identity)" != "$PLANNED_IDENTITY" ]] \
    || [[ "$(worktree_porcelain_entry)" != "$PLANNED_ENTRY" ]]; then
    echo "错误: worktree '${WT}' 状态在确认/等锁期间已变化（被移除或重建），拒绝清理（#1084）。" >&2
    echo "      若确认要清理当前实例，请重跑本脚本。" >&2
    exit 1
fi
if [[ -d "$TARGET" ]]; then
    git worktree remove "$TARGET"
    echo "已移除 worktree: $TARGET"
else
    echo "worktree 不存在（跳过）: $TARGET"
fi

# 2. 本地分支：git branch -d 只允许删已合并分支，未合并报错时提示但不中断
#    后续 DB/bucket 清理。远端分支默认只打印提示命令。
# 远端删除护栏（#930）：长期分支与发布线一律拒删，即便显式传了
# --delete-remote-branch。
is_protected_remote_branch() {
    case "$1" in
        main | master | prod | release/*) return 0 ;;
    esac
    return 1
}
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
    if is_protected_remote_branch "$BRANCH"; then
        echo "提示: $BRANCH 是受保护分支（main/master/prod/release/*），不删除远端分支" >&2
    elif [[ -n "$DELETE_REMOTE" ]]; then
        git push origin --delete "$BRANCH" && echo "已删除远端分支: origin/$BRANCH"
    else
        echo "提示: 如需删除远端分支，执行 git push origin --delete $BRANCH"
        echo "      （或重跑本脚本加 --delete-remote-branch）"
    fi
else
    echo "未解析到分支（跳过本地/远端分支删除）"
fi

# 3. 派生 Postgres 库：在本持锁进程内同步删除（R5 熔断收窄后不再转调 drop
#    子进程——锁持有者即唯一执行者，父进程被 SIGKILL 时不会有孤儿脚本继续
#    推进删除）。删除集合 = 上方锁内重探测且已通过计划比对的结果。取舍说明
#    （熔断重估第 2 轮）：两条 dropdb 是单条快命令，不加租约校验——孤儿
#    dropdb 只完成「删除决策时已存在」的那一条删除，与竞争者重建同名库的
#    交错窗口为毫秒级，归入「在途单命令完成」的声明残余；bucket delete 是
#    多批 S3 调用（窗口长且分批），才需要逐批租约校验（见 run_bucket_phase）。
#    复核失败 fail-closed；撞名检查把已完成初始化的同名登记算冲突（待初始化
#    登记不构成资源主，见 worktree-names-lib.sh）。
worktree_require_unique_derived_names "$ROOT" "$WT" "删除派生库" 1 || exit 1
if [[ "$_WT_DB_FOUND" -gt 0 ]]; then
    worktree_db_drop
else
    echo "没有需要删除的库。"
fi

# 4. 派生 S3 bucket：删除集合同样取自锁内重探测（上方已 probe）。删除前把
#    已完成初始化的同名重建算冲突（#1084）。delete 在本进程前台执行（R5
#    收窄），uv/python 子进程持删除租约逐批校验（见 run_bucket_phase 注释）。
rc=0
if [[ "$bucket_rc" -eq 10 ]]; then
    worktree_require_unique_derived_names "$ROOT" "$WT" "删除派生 bucket" 1 || exit 1
    run_bucket_phase delete || rc=$?
fi
worktree_name_lock_release
if [[ "$rc" -eq 4 ]]; then
    exit 1
elif [[ "$rc" -ne 0 ]]; then
    bucket_phase_degraded "$rc"
fi

echo "完成: worktree '$WT' 收尾清理结束。"
