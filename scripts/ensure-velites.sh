#!/usr/bin/env bash
# velites 二进制新鲜度检测：PATH 上的 velites 是跨 worktree 共享的安装物，
# 「代码已 pull 但二进制还是旧构建」不会触发任何报错。本脚本用 velites/
# 源码树的 git tree hash 做指纹，与二进制旁的 stamp 文件对比，不一致（或
# 二进制缺失）时重新 cargo build --release 并原子替换安装。velites/ 有未
# 提交改动时指纹不可靠，强制重建。make prod-up（原生形态）每次启动前调用本脚本。
#
# 用法：
#   scripts/ensure-velites.sh                    安装/刷新 PATH 上的 velites（默认）
#   scripts/ensure-velites.sh --dest DIR         安装/刷新 DIR/velites（跳过 PATH 探测）
#   scripts/ensure-velites.sh --print-bin-dir    输出无参形态的安装目录后退出
#                                               （服务启动器 prepend PATH 用，不构建）
#
# 无参形态是 #507 收敛后的唯一原生安装通道：velites 装到
# ~/.local/bin（VELITES_INSTALL_DIR 可覆盖），机器级单副本，Worker/Host
# 解析均以 PATH 为权威（data/bin 不再是原生形态的落点）。
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

# 无参形态的安装目录（单一事实源）：PATH 上已有 velites → 维护该副本所在
# 目录；否则装到 VELITES_INSTALL_DIR（默认 ~/.local/bin）。服务启动器
# （native-prod-up.sh / dev_stack.sh）经 --print-bin-dir 查询本函数的结论，
# 不各写一份探测逻辑——两份探测必然在 VELITES_INSTALL_DIR 覆盖等语义上漂移。
velites_install_dir() {
    local on_path
    on_path="$(command -v velites || true)"
    if [[ -n "$on_path" ]]; then
        dirname "$on_path"
    else
        echo "${VELITES_INSTALL_DIR:-$HOME/.local/bin}"
    fi
}

# 查询形态：只输出安装目录，不跑 git 探测/构建（无 git/cargo 的环境也可用）。
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
    echo "警告: velites 已安装到 $VELITES_BIN，但该目录不在当前 PATH 上——本 shell" >&2
    echo "  及其启动的进程解析不到它（Worker/Host 会 fail-closed 或回落 data/bin" >&2
    echo "  存量旧副本）。请把目录加入 PATH（建议写入 shell profile）:" >&2
    echo "    export PATH=\"$(dirname "$VELITES_BIN"):\$PATH\"" >&2
    echo "  注: native-prod-up.sh / dev_stack.sh 启动服务前已自动前置该目录，服务链不受影响。" >&2
}

if [[ -z "$DIRTY" && -x "$VELITES_BIN" && -f "$STAMP" && "$(cat "$STAMP")" == "$SRC_ID" ]]; then
    echo "velites 二进制已是最新（${SRC_ID:0:12}），跳过构建"
    warn_if_unresolvable
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
