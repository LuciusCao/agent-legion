#!/usr/bin/env bash
# 前端依赖新鲜度检测：旧判定只看 frontend/node_modules 是否存在，升级 pull
# 进新的依赖清单（如 v0.7.13 #804 画布化引入 react-rnd）后旧目录被继续
# 使用，新代码按旧依赖构建，tsc 报 TS2307、make prod-up 失败（issue #810）。
# 本脚本以 package.json + package-lock.json 的摘要指纹与 node_modules 内的
# stamp 比对，目录缺失、stamp 缺失或指纹不一致时重新 npm ci。指纹语义同
# scripts/ensure-velites.sh 的 src-stamp：安装成功后才写 stamp；stamp 放在
# node_modules 里，手动删目录时随之消失、天然失效（npm ci 本身也是整目录
# 重建，stamp 不会跨安装存活）。强制重装的逃生口：删 frontend/node_modules。
#
# 失败保护（PR #832 codex P1）：npm ci 自身会先整目录删除 node_modules，
# 网络/registry/lifecycle script 失败时留下空缺或半安装树，原本可用的旧
# 依赖一并丢失。已有 node_modules 时先挪到同卷备份位 .node_modules.bak，
# 任何失败路径（含 Ctrl-C/SIGTERM/errexit 经 EXIT trap）恢复旧目录——升级
# 失败不等于丢失可运行的开发环境；备份窗口内瞬时占用约一倍磁盘。
#
# SIGKILL 自愈（PR #832 codex P2）：强杀后备份位与 node_modules 可能并存，
# 可否弃备份以「新树 stamp 与当前指纹匹配」为唯一判据——stamp 只在本脚本
# 完整安装成功后写入，匹配即新树完整；不匹配（含 stamp 缺失，npm ci 中途
# 被杀）视为半安装残树，删残树、恢复备份。按「目录存在」弃备份会把唯一
# 完整的旧树删掉，随后安装再失败时失败保护已失效。
#
# 并发互斥（PR #832 codex P2）：备份位与恢复逻辑是同 worktree 内的共享
# 可变状态，dev-up / install / prod-up 或手工并发调用会互相移走备份、删除
# 对方刚完成的安装（EXIT trap 恢复的正是对方的成果）。整个事务（残留
# 收编 → 指纹判定 → 安装 → stamp → 清备份）经 mkdir 原子锁串行化，语义
# 同 scripts/gate-queue.sh 的 slot：等待者打印持有者 pid；持有者死亡
# （kill -0）即回收；锁不防跨 worktree——各 worktree 的 frontend/ 互不
# 相干，无需机器级锁。mkdir 与写 pid 之间的窗口只剩相邻两条语句，空 pid
# 残锁经短暂宽限后由等待者回收。
#
# 用法（在仓库根执行，native-prod-up / install-deps / dev_stack 统一委托）：
#   ./scripts/ensure-frontend-deps.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAMP="frontend/node_modules/.deps-stamp"
BACKUP="frontend/.node_modules.bak"
LOCK_DIR="frontend/.deps-install.lock"

for manifest in frontend/package.json frontend/package-lock.json; do
    # ${manifest} 必须带花括号：后随全角逗号时，bash 3.2 在部分 locale 下
    # 会把多字节首字节并入变量名（manifest\xef: unbound variable）。
    if [[ ! -f "$manifest" ]]; then
        echo "缺少 ${manifest}，无法校验前端依赖" >&2
        exit 1
    fi
done

# sha256sum（Linux）与 shasum -a 256（macOS）双兼容（同 install-worker.sh）；
# 两者都缺时显式报错，不给一条 command-not-found 的隐晦失败。
if command -v sha256sum >/dev/null 2>&1; then
    HASH=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
    HASH=(shasum -a 256)
else
    echo "缺少 sha256sum / shasum，无法计算依赖清单指纹" >&2
    exit 1
fi
# npm 缺失时提前报错，避免走一轮备份/恢复循环后混入 command-not-found 噪音。
if ! command -v npm >/dev/null 2>&1; then
    echo "缺少 npm，无法安装前端依赖" >&2
    exit 1
fi
# 两清单各自摘要按行拼接：同时覆盖 package.json 与 lockfile，且避免
# cat 拼接的边界歧义（前文件末尾无换行时内容漂移会产生相同串）。
FINGERPRINT="$("${HASH[@]}" frontend/package.json frontend/package-lock.json | awk '{print $1}')"

# 新树是否完整可信：stamp 与当前指纹匹配。stamp 只在完整安装成功后由
# 本脚本写入——半安装残树（npm ci 中途被杀）没有 stamp，不可能伪造命中。
modules_fresh() {
    [[ -d frontend/node_modules && -f "$STAMP" && "$(cat "$STAMP")" == "$FINGERPRINT" ]]
}

# ---- 并发互斥：mkdir 原子锁 + pid 存活检测 + 死锁回收（语义同 gate-queue）----
acquire_deps_lock() {
    local waited=0 holder
    while ! mkdir "$LOCK_DIR" 2>/dev/null; do
        holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
        if [[ -z "$holder" ]]; then
            # 持有者可能恰在 mkdir 与写 pid 之间（相邻语句）；宽限后仍无
            # pid 视为该窗口内被强杀的残锁，回收。
            if (( waited >= 2 )); then
                echo "检测到无持有者进程的安装锁，回收重试…" >&2
                rm -rf "$LOCK_DIR"
                waited=0
                continue
            fi
        elif ! kill -0 "$holder" 2>/dev/null; then
            echo "检测到陈旧安装锁（pid ${holder} 已退出），回收重试…" >&2
            rm -rf "$LOCK_DIR"
            continue
        fi
        if (( waited % 30 == 0 )); then
            echo "另一进程（pid ${holder:-unknown}）正在安装前端依赖，等待…" >&2
        fi
        sleep 1
        waited=$(( waited + 1 ))
    done
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
}

release_deps_lock() {
    # 仅当锁内 pid 仍是本进程时删除：等待者绝不误删持有者的锁。
    if [[ "$(cat "$LOCK_DIR/pid" 2>/dev/null || true)" == "$$" ]]; then
        rm -rf "$LOCK_DIR"
    fi
}

# EXIT trap：备份位存在（安装路径被中断）则恢复旧目录；随后释放锁。
# trap 内命令失败不改写脚本退出码（除非显式 exit），恢复失败时备份仍在，
# 下次运行的残留收编逻辑自愈。
restore_and_release() {
    if [[ -d "$BACKUP" ]]; then
        rm -rf frontend/node_modules
        mv -f "$BACKUP" frontend/node_modules
    fi
    release_deps_lock
}

# 锁须先于任何共享状态变更（含残留备份收编与跳过路径里的 rm）。
acquire_deps_lock
trap 'restore_and_release' EXIT

# ---- SIGKILL 残留收编：备份与新树并存时以 stamp 有效性裁决 ----
# 新树 stamp 命中当前指纹 = 上次安装已完整完成（stamp 后、清备份前被杀），
# 弃备份；否则新树是半安装残树，删残树、恢复备份——按「目录存在」弃备份
# 会删掉唯一完整的旧树，之后安装再失败时 EXIT trap 恢复的是残树。
if [[ -d "$BACKUP" ]]; then
    if modules_fresh; then
        rm -rf "$BACKUP"
    else
        rm -rf frontend/node_modules
        mv "$BACKUP" frontend/node_modules
    fi
fi

if modules_fresh; then
    echo "前端依赖已是最新（${FINGERPRINT:0:12}），跳过 npm ci"
    exit 0
fi

if [[ -f "$STAMP" ]]; then
    echo "前端依赖清单已变化（${FINGERPRINT:0:12}），重新安装…"
else
    echo "安装前端依赖（npm ci）…"
fi

if [[ -d frontend/node_modules ]]; then
    mv frontend/node_modules "$BACKUP"
    if ! (cd frontend && npm ci); then
        echo "npm ci 失败，已恢复升级前的 node_modules（旧依赖仍可用，稍后重试）" >&2
        exit 1  # EXIT trap 完成恢复
    fi
else
    # 无旧目录可保护：保持裸 npm ci 语义（失败即失败，不留备份残迹）。
    (cd frontend && npm ci)
fi
# stamp 先于备份清理写入：两步之间被强杀时，下次运行凭 stamp 命中弃备份
# （上方收编逻辑）；stamp 写入前被杀则恢复备份重装。npm ci 对零依赖清单
# 成功时不创建 node_modules，mkdir -p 兜底，否则 stamp 写入失败且下轮
# 永远进不了跳过态。
mkdir -p frontend/node_modules
printf '%s\n' "$FINGERPRINT" > "$STAMP"
rm -rf "$BACKUP"
