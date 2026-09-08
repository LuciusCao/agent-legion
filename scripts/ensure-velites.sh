#!/usr/bin/env bash
# velites 二进制新鲜度检测：PATH 上的 velites 是跨 worktree 共享的安装物，
# 「代码已 pull 但二进制还是旧构建」不会触发任何报错。本脚本用 velites/
# 源码树的 git tree hash 做指纹，与二进制旁的 stamp 文件对比，不一致（或
# 二进制缺失）时重新 cargo build --release 并原子替换安装。velites/ 有未
# 提交改动时指纹不可靠，强制重建。make prod-up（原生形态）每次启动前调用本脚本。
#
# 用法：
#   scripts/ensure-velites.sh                    安装/刷新本语境的 velites（默认：dev →
#                                               机器级共享目录；prod worktree 或
#                                               AGENT_LEGION_VELITES_ISOLATED=1 →
#                                               worktree 隔离目录）
#   scripts/ensure-velites.sh --dest DIR         安装/刷新 DIR/velites（跳过 PATH 探测）
#   scripts/ensure-velites.sh --print-bin-dir    输出无参形态的安装目录后退出
#                                               （服务启动器 prepend PATH 用，不构建）
#
# 无参形态是 #507 收敛后的原生安装通道：velites 装到机器级共享目录
# ~/.local/bin（VELITES_INSTALL_DIR 可覆盖），Worker/Host 解析均以 PATH
# 为权威（data/bin 不再是原生形态的落点）。生产 worktree（.worktrees/prod，
# AGENTS.md §1）例外（PR #519 codex P1）：同机维护开发与生产 worktree 时，
# 两侧共写机器级副本意味着一次开发安装就改写生产正在使用的二进制与
# stamp——不经 prod pull、不经重启，任务派生即执行开发版。prod 语境（或
# 显式 env AGENT_LEGION_VELITES_ISOLATED=1）改装按 worktree 名派生的
# 隔离目录 ${XDG_DATA_HOME:-~/.local/share}/agent-legion/<worktree>/bin，
# 且不读 PATH / VELITES_INSTALL_DIR——两者是全 worktree 共享的通道
# （shell profile），正是污染路径。
# --dest 仅供显式安置副本到指定目录（如 Docker 形态把 Release 产物
# 安置到 compose VELITES_BIN 的宿主路径）；本仓库的原生/开发链路
# （install-deps.sh、native-prod-up.sh）不再使用它。二进制按平台构建——
# 给哪台 Worker 用就在同 OS/架构的机器上执行本脚本。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEST_DIR=""
PRINT_BIN_DIR=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)
            DEST_DIR="${2:?--dest 需要目录参数}"
            shift 2
            ;;
        --print-bin-dir)
            PRINT_BIN_DIR=true
            shift
            ;;
        *)
            echo "未知参数：$1（仅支持 --dest DIR、--print-bin-dir）" >&2
            exit 2
            ;;
    esac
done

# --- worktree 语境判定与安装目录派生（PR #519 codex P1：prod 隔离） ---

# 生产 worktree 判定（AGENTS.md §1）：git worktree list --porcelain 的第一个
# 条目是 bare 主仓库根，worktree 是其 .worktrees/ 平级子目录（与
# init-worktree.sh 同一判定法）。git 不可用或布局异常按非 prod 处理——
# 隔离是防污染增强而非安全边界，探测失败回落共享语义即可，不该阻断安装。
is_prod_worktree() {
    local main
    main="$(git worktree list --porcelain 2>/dev/null | awk '/^worktree /{print $2; exit}')"
    [[ -n "$main" && "$(dirname "$ROOT")" == "$main/.worktrees" && "$(basename "$ROOT")" == "prod" ]]
}

# 隔离开关（tri-state）：AGENT_LEGION_VELITES_ISOLATED=1 强制隔离、=0 强制
# 共享（prod worktree 退回机器级语义的显式逃生口）、未设置按 worktree 语境
# 自动判定。非法取值 fail fast——静默按共享处理会让 prod 污染无声回归
# （如把 1 误写成 true）。
isolated_mode() {
    case "${AGENT_LEGION_VELITES_ISOLATED:-}" in
        1) return 0 ;;
        0) return 1 ;;
        "") is_prod_worktree ;;
        *)
            echo "错误: AGENT_LEGION_VELITES_ISOLATED=${AGENT_LEGION_VELITES_ISOLATED} 非法（仅支持 1 / 0 / 未设置）" >&2
            exit 2
            ;;
    esac
}

# 隔离安装目录：按 worktree 名派生（~/.local/share/agent-legion/<name>/bin，
# XDG_DATA_HOME 可重定位）。worktree 名 sanitize 后目录名可读、按 worktree
# 天然互不冲突；整棵 agent-legion/ 前缀树聚集，清理时可发现。
velites_isolated_dir() {
    local name
    name="$(printf '%s' "$(basename "$ROOT")" | tr -c 'a-zA-Z0-9_' '_')"
    echo "${XDG_DATA_HOME:-$HOME/.local/share}/agent-legion/${name}/bin"
}

# 无参形态的安装目录（单一事实源）：服务启动器（native-prod-up.sh /
# dev_stack.sh）经 --print-bin-dir 查询本函数的结论，不各写一份探测逻辑
# ——两份探测必然在覆盖语义上漂移。优先级：隔离开关（显式 env > prod
# 语境）> PATH 已有副本 > VELITES_INSTALL_DIR（默认 ~/.local/bin）。
# - 隔离形态不读 PATH / VELITES_INSTALL_DIR：PATH 上已有的可能是机器级
#   共享副本（dev 维护的），prod 语境「维护已有副本」维护的正是污染源。
# - 共享形态（dev）维持 #507 语义：PATH 上已有 velites → 维护该副本所在
#   目录；例外是隔离目录树（agent-legion/<name>/bin）里的副本——那是其他
#   worktree 的私有运行时（典型：用户手工把 prod 的隔离目录加进了 PATH），
#   dev 维护它等于反向污染（开发构建写进生产运行时副本），跳过、回落共享目录。
velites_install_dir() {
    local on_path base
    if isolated_mode; then
        velites_isolated_dir
        return
    fi
    on_path="$(command -v velites || true)"
    base="${XDG_DATA_HOME:-$HOME/.local/share}/agent-legion"
    if [[ -n "$on_path" && "$(dirname "$on_path")" == "$base/"*"/bin" ]]; then
        on_path=""
    fi
    if [[ -n "$on_path" ]]; then
        dirname "$on_path"
    else
        echo "${VELITES_INSTALL_DIR:-$HOME/.local/bin}"
    fi
}

# 查询形态：只输出安装目录，不跑源码指纹探测/构建；worktree 语境判定需要
# git（不可用时按共享语义回落，不失败）。
if $PRINT_BIN_DIR; then
    velites_install_dir
    exit 0
fi

SRC_ID="$(git rev-parse HEAD:velites)"
DIRTY="$(git status --porcelain -- velites)"

if [[ -n "$DEST_DIR" ]]; then
    VELITES_BIN="${DEST_DIR%/}/velites"
else
    VELITES_BIN="$(velites_install_dir)/velites"
fi
STAMP="${VELITES_BIN}.src-stamp"

# 安装侧守门（无参形态，PR #519 codex P1）：安装目录不在调用方 PATH 上时
# 向 stderr 打明确指引，杜绝「装了但当前环境永远解析不到」的静默态——全新
# 机器找不到 velites，保留 data/bin 存量副本的机器则继续命中过期副本（正是
# #507 要修的漂移）。不 fail（退出码 0）：存量机器可能仍靠 data/bin 兜底在
# 服务，交互场景误伤不值得；服务启动链已自动 prepend 安装目录（见指引末行）。
warn_if_unresolvable() {
    [[ -n "$DEST_DIR" ]] && return 0  # --dest 是显式安置（Docker 外挂），不期望 PATH 命中
    local resolved
    resolved="$(command -v velites || true)"
    [[ "$resolved" == "$VELITES_BIN" ]] && return 0
    if isolated_mode; then
        # 隔离形态：shell 解析到的可能是机器级共享副本（开发侧构建的版本
        # 可能不同），也可能什么都解析不到——两种情况服务链都不受影响
        # （native-prod-up.sh 经 --print-bin-dir 前置隔离目录），但提示措辞
        # 必须区分：前者是版本来源问题，后者才谈得上「解析不到」。
        echo "警告: 本 worktree 的 velites 隔离副本在 $VELITES_BIN，当前 shell 未优先解析它" >&2
        [[ -z "$resolved" ]] || echo "  （当前解析到 $resolved——非本 worktree 的隔离副本，" >&2
        [[ -z "$resolved" ]] || echo "    版本可能来自开发侧构建，与本 worktree 不一致）" >&2
        echo "  服务链不受影响：native-prod-up.sh 启动服务前已前置隔离目录。" >&2
        echo "  交互使用可临时 export PATH=\"$(dirname "$VELITES_BIN"):\$PATH\"（不建议写入 profile——" >&2
        echo "  会让开发 shell 也解析到生产副本）。" >&2
        return 0
    fi
    echo "警告: velites 已安装到 $VELITES_BIN，但该目录不在当前 PATH 上——本 shell" >&2
    echo "  及其启动的进程解析不到它（Worker/Host 会 fail-closed 或回落 data/bin" >&2
    echo "  存量旧副本）。请把目录加入 PATH（建议写入 shell profile）:" >&2
    echo "    export PATH=\"$(dirname "$VELITES_BIN"):\$PATH\"" >&2
    echo "  注: native-prod-up.sh / dev_stack.sh 启动服务前已自动前置该目录，服务链不受影响。" >&2
}

# 迁移提示（仅隔离形态）：机器级共享位置（VELITES_INSTALL_DIR，默认
# ~/.local/bin）已存在的副本不再被本 worktree 使用——不代删：它仍由开发侧
# make install 维护，只有这台机器再无开发 worktree 时才是无主文件（与
# install-deps.sh 对 data/bin 存量副本的同款手法）。
warn_if_legacy_shared_copy() {
    [[ -z "$DEST_DIR" ]] || return 0
    isolated_mode || return 0
    local shared="${VELITES_INSTALL_DIR:-$HOME/.local/bin}/velites"
    # 病态组合守卫：VELITES_INSTALL_DIR 恰好指到隔离目录本身时，那份副本
    # 就是本 worktree 正在使用的，不是遗留共享副本。
    [[ "$shared" == "$VELITES_BIN" ]] && return 0
    [[ -e "$shared" ]] || return 0
    echo "提示: 本 worktree 已改用隔离安装目录，机器级共享副本 $shared 不再被它使用" >&2
    echo "  （共享副本由开发侧 make install 继续维护；确认本机已无开发 worktree 在用后" >&2
    echo "   可清理：rm -f $shared $shared.src-stamp）" >&2
}

if [[ -z "$DIRTY" && -x "$VELITES_BIN" && -f "$STAMP" && "$(cat "$STAMP")" == "$SRC_ID" ]]; then
    echo "velites 二进制已是最新（${SRC_ID:0:12}），跳过构建"
    warn_if_unresolvable
    warn_if_legacy_shared_copy
    exit 0
fi

if ! command -v cargo >/dev/null 2>&1; then
    echo "velites 需要重建（源码指纹 ${SRC_ID:0:12}）但 cargo 不可用" >&2
    exit 1
fi

if [[ -n "$DIRTY" ]]; then
    echo "velites/ 有未提交改动，强制重新构建…"
else
    echo "velites 源码已更新（${SRC_ID:0:12}），重新构建…"
fi
(cd velites && cargo build --release --locked)

# 原子替换：运行中的 worker 继续用旧 inode，新派生的 agent 进程立即拿到新
# 二进制；直接覆盖写入可能让并发生成的进程读到截断的二进制。
mkdir -p "$(dirname "$VELITES_BIN")"
tmp="${VELITES_BIN}.tmp.$$"
trap 'rm -f "$tmp"' EXIT
cp velites/target/release/velites "$tmp"
chmod +x "$tmp"
mv -f "$tmp" "$VELITES_BIN"
echo "$SRC_ID" > "$STAMP"
echo "velites 已安装到 $VELITES_BIN"
warn_if_unresolvable
warn_if_legacy_shared_copy
