#!/usr/bin/env bash
# velites 二进制新鲜度检测与安置。
#
# 「安置在哪、什么算新鲜」不再由本脚本自行判断（#835 四轮 codex 评审的
# 根源：bash 里手写的查找逻辑与 Python resolver 各持一份模型，每个维度
# 失同步就是一轮静默滞留——#831 漏 data/bin 通道、fast-path 短路、PATH
# 目录分叉、漏刷 velites-sandbox）。决策面统一在 scripts/velites_deploy_plan.py：
# 它 import 真实 resolver（worker/binary_resolution、shared/code_sandbox、
# worker/runtime/catalog），从解析序推导安置目标，家族（velites +
# 已存在的 velites-sandbox）共享同一 src-stamp 指纹。本脚本退化为
# 「git 指纹 → planner 判鲜 → cargo build → 按 planner 目标原子安置」。
#
# 用法：
#   scripts/ensure-velites.sh              刷新 PATH 通道（velites 现有位置，
#                                         无 PATH 副本时落 VELITES_INSTALL_DIR
#                                         或 ~/.local/bin；PATH 上独立发现的
#                                         家族成员位置一并刷新）
#   scripts/ensure-velites.sh --dest DIR   刷新 DIR（自带副本通道：DIR 内的
#                                         velites 与已有存在痕迹的家族成员）
#
# 两通道各管一段；make prod-up（原生形态）先后都跑（#831）。velites/ 有未
# 提交改动时指纹不可靠，强制重建。二进制按平台构建——给哪台 Worker 用就在
# 同 OS/架构的机器上执行本脚本。
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

# planner 的 python：显式覆盖（测试）> 仓库 venv（install-deps/prod-up 都先
# 跑 uv sync，.venv 必在）> 裸 python3（无 tomllib 时 planner 会给出明确
# 报错与指引）。python 选择不影响安置语义——planner 只做决策。
# 退出码检查：planner 崩溃（删失/损坏/解释器缺 tomllib）时**必须中止脚本**——
# 判鲜输出为空与「全部新鲜」在 $() 里无法区分，吞掉失败码会让脚本静默
# 跳过刷新（把决策面退化回「脚本自己猜」的旧模型，正是 #835 要消灭的）。
_plan_python() {
    if [[ -n "${VELITES_PLAN_PYTHON:-}" ]]; then
        printf '%s\n' "${VELITES_PLAN_PYTHON}"
    elif [[ -x "$ROOT/.venv/bin/python" ]]; then
        printf '%s\n' "$ROOT/.venv/bin/python"
    else
        printf '%s\n' "python3"
    fi
}

# 退出码经全局 PLAN_RC/PLAN_OUTPUT 返回——调用点必须**在 $() 之外**直接
# 调用本函数：命令替换的子 shell 里 exit 退不出主脚本，判鲜失败会被
# `[[ -z ]]` 当成「全部新鲜」吞掉（静默跳过刷新，#835 要消灭的形态）。
_run_plan() {
    local python
    python="$(_plan_python)"
    PLAN_OUTPUT="$("$python" scripts/velites_deploy_plan.py "$@" 2>&1)"
    PLAN_RC=$?
    if [[ "$PLAN_RC" -ne 0 ]]; then
        echo "velites_deploy_plan.py 执行失败（rc=$PLAN_RC）：$PLAN_OUTPUT" >&2
        exit 1
    fi
}

if [[ -n "$DEST_DIR" ]]; then
    mkdir -p "$DEST_DIR"
    DEST_ARGS=(--dest "$DEST_DIR")
else
    DEST_ARGS=()
fi

SRC_ID="$(git rev-parse HEAD:velites)"
DIRTY="$(git status --porcelain -- velites)"

# 家族级判鲜：任一目标位置的 bin 缺失或 stamp 不符（含无 stamp 的 Release
# 产物/手工安置）即整族重建——按单工件判鲜会让 fast-path 跳过同目录里
# 候选序更优先的旧 velites-sandbox（#835 codex P2）。
if [[ -z "$DIRTY" ]]; then
    _run_plan check --src-id "$SRC_ID" ${DEST_ARGS[@]+"${DEST_ARGS[@]}"}
    if [[ -z "$PLAN_OUTPUT" ]]; then
        echo "velites 二进制已是最新（${SRC_ID:0:12}），跳过构建"
        exit 0
    fi
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
_run_plan plan ${DEST_ARGS[@]+"${DEST_ARGS[@]}"}
while IFS='|' read -r bin target; do
    [[ -z "$bin" ]] && continue
    if [[ -e "$target" && ! -f "${target}.src-stamp" ]]; then
        echo "提示: $target 存在但无 src-stamp 指纹（Release 产物/手工安置？），将按源码指纹重建并覆盖" >&2
    fi
done <<<"$PLAN_OUTPUT"

if [[ -n "$DIRTY" ]]; then
    echo "velites/ 有未提交改动，强制重新构建…"
else
    echo "velites 源码已更新（${SRC_ID:0:12}），重新构建…"
fi
(cd velites && cargo build --release --locked)

# 安置：planner 给出的每个目标位置原子替换 + 同批 stamp。候选序
# velites-sandbox 优先于 velites——按名字序安置保证同目录内 velites
# 先落位、家族成员后落位，解析永远落在刷新后的副本上。
while IFS='|' read -r bin target; do
    [[ -z "$bin" ]] && continue
    src="velites/target/release/$bin"
    if [[ ! -f "$src" ]]; then
        echo "错误：构建产物缺失 $src（velites/Cargo.toml 的 [[bin]] 与安置面不一致？）" >&2
        exit 1
    fi
    # 原子替换：运行中的 worker 继续用旧 inode，新派生的 agent 进程立即拿到
    # 新二进制；直接覆盖写入可能让并发生成的进程读到截断的二进制。
    mkdir -p "$(dirname "$target")"
    tmp="${target}.tmp.$$"
    trap 'rm -f "$tmp"' EXIT
    cp "$src" "$tmp"
    chmod +x "$tmp"
    mv -f "$tmp" "$target"
    echo "$SRC_ID" > "${target}.src-stamp"
    echo "velites 家族成员 $bin 已安装到 $target"
done <<<"$PLAN_OUTPUT"
