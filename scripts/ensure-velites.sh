#!/usr/bin/env bash
# velites 二进制新鲜度检测：PATH 上的 velites 是跨 worktree 共享的安装物，
# 「代码已 pull 但二进制还是旧构建」不会触发任何报错。本脚本用 velites/
# 源码树的 git tree hash 做指纹，与二进制旁的 stamp 文件对比，不一致（或
# 二进制缺失）时重新 cargo build --release 并原子替换安装。velites/ 有未
# 提交改动时指纹不可靠，强制重建。native-prod-up 每次启动前调用本脚本。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SRC_ID="$(git rev-parse HEAD:velites)"
DIRTY="$(git status --porcelain -- velites)"

# 测试可用 VELITES_INSTALL_DIR 覆盖默认安装目录（PATH 上无 velites 时生效）。
VELITES_BIN="$(command -v velites || true)"
if [[ -z "$VELITES_BIN" ]]; then
    VELITES_BIN="${VELITES_INSTALL_DIR:-$HOME/.local/bin}/velites"
fi
STAMP="${VELITES_BIN}.src-stamp"

if [[ -z "$DIRTY" && -x "$VELITES_BIN" && -f "$STAMP" && "$(cat "$STAMP")" == "$SRC_ID" ]]; then
    echo "velites 二进制已是最新（${SRC_ID:0:12}），跳过构建"
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
