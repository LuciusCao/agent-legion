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
# 依赖一并丢失（改动前的判定从不触碰已存在的 node_modules，这是本脚本
# 引入的新失败面）。已有 node_modules 时先挪到同卷备份位 .node_modules.bak，
# 任何失败路径（含 Ctrl-C/SIGTERM/errexit 经 EXIT trap）恢复旧目录——升级
# 失败不等于丢失可运行的开发环境；备份窗口内瞬时占用约一倍磁盘。被
# SIGKILL 强杀（trap 无从执行）残留的备份由下次运行开头的清理逻辑收编。
#
# 用法（在仓库根执行，native-prod-up / install-deps / dev_stack 统一委托）：
#   ./scripts/ensure-frontend-deps.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAMP="frontend/node_modules/.deps-stamp"
BACKUP="frontend/.node_modules.bak"

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

# 上次运行被 SIGKILL 等强杀（EXIT trap 无从执行）时可能残留备份位：
# - 备份在、node_modules 不在（npm ci 已清原目录、新装未完成）：先恢复
#   备份，下方按指纹决定是否重装（恢复出的旧 stamp 与当前清单不匹配时
#   会再走一次带备份的安装，语义完整）。
# - 备份与 node_modules 并存（安装成功后、备份清理前被杀）：备份已陈旧，
#   直接删除，随后指纹命中即跳过。
if [[ -d "$BACKUP" ]]; then
    if [[ -d frontend/node_modules ]]; then
        rm -rf "$BACKUP"
    else
        mv "$BACKUP" frontend/node_modules
    fi
fi

if [[ -d frontend/node_modules && -f "$STAMP" && "$(cat "$STAMP")" == "$FINGERPRINT" ]]; then
    echo "前端依赖已是最新（${FINGERPRINT:0:12}），跳过 npm ci"
    exit 0
fi

if [[ -f "$STAMP" ]]; then
    echo "前端依赖清单已变化（${FINGERPRINT:0:12}），重新安装…"
else
    echo "安装前端依赖（npm ci）…"
fi

if [[ -d frontend/node_modules ]]; then
    # 恢复 trap 先于 mv 设置：mv 被打断时备份位可能尚未成形，trap 以备份
    # 位存在性自守（不存在即无事可恢复，原目录仍完整）。
    trap 'if [[ -d frontend/.node_modules.bak ]]; then rm -rf frontend/node_modules; mv -f frontend/.node_modules.bak frontend/node_modules; fi' EXIT
    mv frontend/node_modules "$BACKUP"
    if ! (cd frontend && npm ci); then
        echo "npm ci 失败，已恢复升级前的 node_modules（旧依赖仍可用，稍后重试）" >&2
        exit 1  # EXIT trap 完成恢复
    fi
else
    # 无旧目录可保护：保持裸 npm ci 语义（失败即失败，不留备份残迹）。
    (cd frontend && npm ci)
fi
# stamp 先于备份清理写入：两步之间被强杀时，下次运行凭 stamp 命中跳过，
# 残留备份由开头的陈旧备份清理收走。npm ci 对零依赖清单成功时不创建
# node_modules，mkdir -p 兜底，否则 stamp 写入失败且下轮永远进不了跳过态。
mkdir -p frontend/node_modules
printf '%s\n' "$FINGERPRINT" > "$STAMP"
trap - EXIT
rm -rf "$BACKUP"
