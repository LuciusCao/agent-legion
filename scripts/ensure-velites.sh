#!/usr/bin/env bash
# velites 二进制新鲜度检测：PATH 上的 velites 是跨 worktree 共享的安装物，
# 「代码已 pull 但二进制还是旧构建」不会触发任何报错。本脚本用 velites/
# 源码树的 git tree hash 做指纹，与二进制旁的 stamp 文件对比，不一致（或
# 二进制缺失）时重新 cargo build --release 并原子替换安装；已有
# velites-sandbox（沙箱包装器，#383 起的独立 bin，解析序优先于 velites）
# 的目录会一并刷新并共享同一 stamp（#835 codex P2）。make prod-up
# （原生形态）每次启动前调用本脚本**两次**——PATH 模式与 --dest data/bin
# （#831：Worker 解析自带副本优先，两处安置点都要刷新才算升级生效）。
#
# 用法：
#   scripts/ensure-velites.sh              安装/刷新 PATH 上的 velites（默认）
#   scripts/ensure-velites.sh --dest DIR   安装/刷新 DIR/velites（跳过 PATH 探测）
#
# --dest 用于 Worker 自带沙箱副本：--dest data/bin 把二进制安置到
# data/bin/velites（Worker 解析顺序：自带副本优先于 PATH，见
# worker/binary_resolution.py resolve_binary；该副本同时是裸机形态的沙箱
# 包装器，Host 侧 shared/code_sandbox.py 解析同一目录）。二进制按平台
# 构建——给哪台 Worker 用就在同 OS/架构的机器上执行本脚本。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEST_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dest)
            DEST_DIR="${2:?--dest 需要目录参数}"
            shift 2
            ;;
        *)
            echo "未知参数：$1（仅支持 --dest DIR）" >&2
            exit 2
            ;;
    esac
done

SRC_ID="$(git rev-parse HEAD:velites)"
DIRTY="$(git status --porcelain -- velites)"

if [[ -n "$DEST_DIR" ]]; then
    VELITES_BIN="${DEST_DIR%/}/velites"
else
    # 测试可用 VELITES_INSTALL_DIR 覆盖默认安装目录（PATH 上无 velites 时生效）。
    VELITES_BIN="$(command -v velites || true)"
    if [[ -z "$VELITES_BIN" ]]; then
        VELITES_BIN="${VELITES_INSTALL_DIR:-$HOME/.local/bin}/velites"
    fi
fi
STAMP="${VELITES_BIN}.src-stamp"

if [[ -z "$DIRTY" && -x "$VELITES_BIN" && -f "$STAMP" && "$(cat "$STAMP")" == "$SRC_ID" ]]; then
    echo "velites 二进制已是最新（${SRC_ID:0:12}），跳过构建"
    exit 0
fi

if ! command -v cargo >/dev/null 2>&1; then
    # 不建议「下载 Release 产物 + 手写当前 HEAD 指纹 stamp」：velites 与
    # 仓库版本线解耦，产物源码往往旧于本 checkout——伪造 stamp 会让新鲜
    # 检查与启动对账（staleness）双双放行，静默运行旧二进制（codex P2 on #835）。
    echo "velites 需要重建（源码指纹 ${SRC_ID:0:12}）但 cargo 不可用——安装 Rust 工具链（https://rustup.rs）后重跑；或在与本机同 OS/架构、同一仓库状态的机器上执行 scripts/ensure-velites.sh 后，把二进制与 .src-stamp 一起拷贝过来（stamp 必须与产物同源，不得手写本仓库指纹；见 docs/agent-worker-deployment.md §5）" >&2
    exit 1
fi

# #831 可见性：既有二进制但无 stamp（Release 产物/手工安置）走的是重建
# 分支——不是过期，是「无法判鲜」。替换经过校验的 Release 二进制前先提示，
# 无对账依据的静默覆盖是运维盲区。
if [[ -e "$VELITES_BIN" && ! -f "$STAMP" ]]; then
    echo "提示: $VELITES_BIN 存在但无 src-stamp 指纹（Release 产物/手工安置？），将按源码指纹重建并覆盖" >&2
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

# #835（codex P2）：沙箱包装器 velites-sandbox 与 velites 同源同指纹。
# cargo build --release 已产出两个 bin（velites/Cargo.toml 的 [[bin]]），
# 这里一并安置并共享同一 src-stamp——shared/code_sandbox.py 的
# resolve_sandbox_binary 候选序是 velites-sandbox 优先：裸机 PATH 或
# data/bin 若存在旧 velites-sandbox，只刷 velites 会让 code 沙箱
# （Host 与 Worker 的 code 节点）继续用旧包装器，沙箱修复静默失效——
# 与 #831 同构的漂移。两 bin 一个指纹：freshness/对账把整个 velites
# 构建产物视为一个单元（重构建时 velites-sandbox 必然同批产出）。
# 部署面矩阵（消费 ⊆ 安置 ⊆ 构建）由 tests/scripts/test_ensure_velites.py
# 的 deploy-matrix 契约测试钉死：新增 bin 或解析侧开始消费新名字而脚本
# 未同步安置时直接红。
SANDBOX_BIN="$(dirname "$VELITES_BIN")/velites-sandbox"
SANDBOX_SRC="velites/target/release/velites-sandbox"
SANDBOX_STAMP="${SANDBOX_BIN}.src-stamp"
if [[ -e "$SANDBOX_BIN" || -e "$SANDBOX_STAMP" ]]; then
    # 候选序 velites-sandbox 优先意味着：目录里存在它就会盖住刚刷新的
    # velites——存在即必须刷新，否则本脚本制造的正是 #831 修复的静默
    # 滞留。不存在则不主动创造（裸机默认走 velites 兜底；docker 镜像的
    # velites-sandbox 在 /usr/local/bin，不经本脚本）。
    if [[ ! -x "$SANDBOX_BIN" || "$(cat "$SANDBOX_STAMP" 2>/dev/null)" != "$SRC_ID" ]]; then
        sandbox_tmp="${SANDBOX_BIN}.tmp.$$"
        cp "$SANDBOX_SRC" "$sandbox_tmp"
        chmod +x "$sandbox_tmp"
        mv -f "$sandbox_tmp" "$SANDBOX_BIN"
        echo "$SRC_ID" > "$SANDBOX_STAMP"
        echo "velites-sandbox（沙箱包装器）已同步安装到 $SANDBOX_BIN"
    fi
fi

echo "$SRC_ID" > "$STAMP"
echo "velites 已安装到 $VELITES_BIN"
