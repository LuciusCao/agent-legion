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
# SIGKILL 残留裁决（PR #832 codex P2）：强杀后备份位与 node_modules 可能
# 并存，可否弃备份以「新树 stamp 与当前指纹匹配」为唯一判据——stamp 只在
# 本脚本完整安装成功后写入，匹配即新树完整；不匹配（含 stamp 缺失，npm ci
# 中途被杀）视为半安装残树，删残树、恢复备份。按「目录存在」弃备份会把
# 唯一完整的旧树删掉，随后安装再失败时失败保护已失效。
#
# 并发互斥（PR #832 codex P2，第三轮）：备份位与恢复逻辑是同 worktree 内
# 的共享可变状态，并发调用会互相移走备份、删除对方刚完成的安装。前两轮
# 的自管 mkdir 目录锁接连暴露观察者侧缺陷——mkdir 与写 pid 之间有空窗
# （需要宽限计时），宽限计数跨持有者累计会误删新持有者刚建的锁，共享
# 路径上 check-then-act 的回收还有不可闭合的 TOCTOU。第三轮改用内核托管
# flock：锁的生命周期归内核——持有者死亡（含 SIGKILL）自动释放，无残锁、
# 无宽限计时、无观察者状态；等待者阻塞在 flock 上而非轮询共享路径，不存
# 在误删他人锁的代码路径。锁 fd 经 exec 传入重入的 bash（flock 属 open
# file description，跨 exec 存活，进程退出即释放）；
# ENSURE_FRONTEND_DEPS_LOCK_HELD 标记防无限重入（同 gate-queue 的
# SLOT_HELD 信任模型）。python3 是三个调用路径（install-deps /
# native-prod-up / dev_stack）的既有前置依赖，缺失即 fail-fast。
#
# 用法（在仓库根执行，native-prod-up / install-deps / dev_stack 统一委托）：
#   ./scripts/ensure-frontend-deps.sh
set -euo pipefail

# 本脚本的绝对路径：必须在下方 cd "$ROOT" 之前、cwd 仍是调用目录时解析
# ——python3 持锁段 exec 重入要用它，cd 之后 $0 的相对形态（frontend/
# 下的 ../scripts/…）按新 cwd 解析会指错位置（真实链路验证抓到过）。
SCRIPT_ABS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
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
# flock 持锁需要 python3（install-deps 已把它列为前置依赖，三个调用路径同源）。
if ! command -v python3 >/dev/null 2>&1; then
    echo "缺少 python3，无法持有安装锁（并发保护必需）" >&2
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

# ---- 并发互斥：flock（python3 持锁后 exec 重入，锁归内核托管）----
# 锁覆盖整个事务（残留收编 → 指纹判定 → 安装 → stamp → 清备份）；上方
# 只读的 fail-fast 检查在锁外执行（不触碰共享状态）。等待者先以非阻塞
# 探测：失败即有持有者，打印提示后阻塞等待（内核唤醒，无轮询）。
# 脚本绝对路径与锁路径都由 bash 在 cd "$ROOT" 之前解析传入：python3 段
# 运行时 cwd 已是仓库根，自行 abspath($0) 会把相对调用路径（frontend/
# 下的 ../scripts/…）按错误基准解析到仓库外（真实链路验证抓到过）。
if [[ "${ENSURE_FRONTEND_DEPS_LOCK_HELD:-}" != "1" ]]; then
    exec python3 - "$SCRIPT_ABS" "$BASH" "$ROOT" <<'PY'
import fcntl
import os
import sys

script, bash, root = sys.argv[1], sys.argv[2], sys.argv[3]
lock_path = os.path.join(root, "frontend", ".deps-install.lock")
try:
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("另一进程正在安装前端依赖，等待…", file=sys.stderr, flush=True)
        fcntl.flock(fd, fcntl.LOCK_EX)
except OSError as exc:
    print(f"无法持有安装锁 {lock_path}: {exc}", file=sys.stderr)
    sys.exit(1)
# execv 继承当前进程环境（os.environ 的修改已同步到 C environ）与已打开
# 的锁 fd（flock 属 open file description，跨 exec 存活直到进程退出）。
# set_inheritable 必不可少：PEP 446 起 os.open 的 fd 默认 close-on-exec，
# 不显式放开则锁 fd 在 execv 瞬间被关闭——锁立即失效（并发窗口重开，
# 行为级并发用例实测抓到过）。
os.set_inheritable(fd, True)
os.environ["ENSURE_FRONTEND_DEPS_LOCK_HELD"] = "1"
os.execv(bash, [bash, script])
PY
fi

# EXIT trap：备份位存在（安装路径被中断）则恢复旧目录。锁无需显式释放：
# flock 归内核托管，本进程以任何方式退出（含 SIGKILL）即自动释放，锁文件
# 残留不阻塞后续运行。
restore_backup_on_exit() {
    if [[ -d "$BACKUP" ]]; then
        rm -rf frontend/node_modules
        mv -f "$BACKUP" frontend/node_modules
    fi
}
trap 'restore_backup_on_exit' EXIT

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
