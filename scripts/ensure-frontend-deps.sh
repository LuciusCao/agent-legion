#!/usr/bin/env bash
# 前端依赖新鲜度检测：旧判定只看 frontend/node_modules 是否存在，升级 pull
# 进新的依赖清单（如 v0.7.13 #804 画布化引入 react-rnd）后旧目录被继续
# 使用，新代码按旧依赖构建，tsc 报 TS2307、make prod-up 失败（issue #810）。
# 本脚本以 package.json + package-lock.json 的摘要指纹与 node_modules 内的
# stamp 比对，目录缺失、stamp 缺失（安装中断，下次自愈）或指纹不一致时
# 重新 npm ci。指纹语义同 scripts/ensure-velites.sh 的 src-stamp：安装成功
# 后才写 stamp；stamp 放在 node_modules 里，手动删目录时随之消失、天然
# 失效（npm ci 本身也是整目录重建，stamp 不会跨安装存活）。
#
# 用法（在仓库根执行，native-prod-up / install-deps / dev_stack 统一委托）：
#   ./scripts/ensure-frontend-deps.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

STAMP="frontend/node_modules/.deps-stamp"

for manifest in frontend/package.json frontend/package-lock.json; do
    # ${manifest} 必须带花括号：后随全角逗号时，bash 3.2 在部分 locale 下
    # 会把多字节首字节并入变量名（manifest\xef: unbound variable）。
    if [[ ! -f "$manifest" ]]; then
        echo "缺少 ${manifest}，无法校验前端依赖" >&2
        exit 1
    fi
done

# sha256sum（Linux）与 shasum -a 256（macOS）双兼容（同 install-worker.sh）。
if command -v sha256sum >/dev/null 2>&1; then
    HASH=(sha256sum)
else
    HASH=(shasum -a 256)
fi
# 两清单各自摘要按行拼接：同时覆盖 package.json 与 lockfile，且避免
# cat 拼接的边界歧义（前文件末尾无换行时内容漂移会产生相同串）。
FINGERPRINT="$("${HASH[@]}" frontend/package.json frontend/package-lock.json | awk '{print $1}')"

if [[ -d frontend/node_modules && -f "$STAMP" && "$(cat "$STAMP")" == "$FINGERPRINT" ]]; then
    echo "前端依赖已是最新（${FINGERPRINT:0:12}），跳过 npm ci"
    exit 0
fi

if [[ -f "$STAMP" ]]; then
    echo "前端依赖清单已变化（${FINGERPRINT:0:12}），重新安装…"
else
    echo "安装前端依赖（npm ci）…"
fi
(cd frontend && npm ci)
printf '%s\n' "$FINGERPRINT" > "$STAMP"
