#!/usr/bin/env bash
# 原子化生产环境重启（issue #629）：down → up → 健康检查做成一个单元，
# 供人工在宿主机终端执行。背景：Studio agent 会话里跑 `make prod-down &&
# make prod-up` 中途断线会留下「已 down 未 up」的生产停摆——凡打断后无法
# 自恢复的操作都应留在人手里（agent 侧由 terminal_guard.py 的禁止清单
# 拦截并指路到本脚本；本脚本暂不接入 agent 允许面）。
#
# 语义：
#   - down 与 up 不间断衔接；up 失败自动重试（默认 3 次，PROD_RESTART_UP_RETRIES
#     覆盖），每次重试前先清残留监听（上一次半启的进程）；
#   - 最终失败打印诊断与手动恢复指引，退出非零——重启失败是显式事件，
#     不静默吞掉；
#   - 幂等可重跑：down 幂等（未在运行则跳过），up 幂等（已在监听则跳过），
#     重跑等价于再走一遍同一单元。
#   - 端口/绑定地址变量与 native-prod-down.sh / native-prod-up.sh 同源
#     （NATIVE_BACKEND_PORT / NATIVE_WORKER_PORT / NATIVE_BACKEND_BIND /
#     NATIVE_WORKER_BIND），up 内部按同一组值探测，不会因变量漂移杀错目标。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

UP_RETRIES="${PROD_RESTART_UP_RETRIES:-3}"
UP_RETRY_WAIT_SECONDS="${PROD_RESTART_UP_RETRY_WAIT_SECONDS:-10}"

echo "== 生产环境原子重启（down → up → 健康检查，失败自动重试）=="

# down：native-prod-down.sh 幂等（目标监听不存在则跳过）；未在运行时它只
# 打印「跳过」并成功返回，重跑安全。down 失败（进程无视 SIGTERM 超时）时
# 服务多半仍在运行——退出并交给人工决断，不带病继续 up（否则 up 的幂等
# 判定会把残留进程误认为「已在运行」，健康检查对旧进程误报就绪）。
if ! ./scripts/native-prod-down.sh; then
    echo "错误: prod-down 未完全成功（有进程未在宽限期内退出）。服务可能部分仍在运行，请人工检查后再重启。" >&2
    exit 1
fi

# up + 健康检查重试：native-prod-up.sh 自带幂等（监听存在即跳过启动）与
# 就绪等待（内部健康轮询）。失败时不能盲目直接重试——半启动的进程可能占
# 着端口，下一轮 up 的幂等判定会误判「已在运行」——先清掉本轮残留再试；
# 清理用的 down 失败不中断重试（残留进程下一轮健康检查自然暴露）。
attempt=1
up_rc=0
while true; do
    echo "== 启动尝试 ${attempt}/${UP_RETRIES} =="
    if ./scripts/native-prod-up.sh; then
        echo "== 重启完成：生产环境已就绪 =="
        exit 0
    else
        up_rc=$?
    fi
    if (( attempt >= UP_RETRIES )); then
        break
    fi
    echo "警告: 启动尝试 ${attempt} 失败（退出码 ${up_rc}），清理残留后 ${UP_RETRY_WAIT_SECONDS}s 重试" >&2
    if ! ./scripts/native-prod-down.sh; then
        echo "警告: 清理残留进程未完全成功（见上方输出），继续重试" >&2
    fi
    sleep "$UP_RETRY_WAIT_SECONDS"
    attempt=$((attempt + 1))
done

# 走到这里：down 已完成、全部 up 尝试失败。这是不可静默的显式故障——
# 打印诊断与手动恢复指引后以非零退出。
cat >&2 <<EOF
错误: 生产环境重启失败——服务当前处于停止状态，需人工介入（已尝试 ${UP_RETRIES} 次）。

诊断步骤：
  1. 查看日志: data/logs/prod-backend.log 与 data/logs/prod-worker.log 的最后数百行
  2. 检查端口占用: lsof -nP -iTCP:8000 -iTCP:8787 -sTCP:LISTEN
  3. 常见原因: 端口被无关进程占用 / 数据库连接失败 / 前端构建失败 / 磁盘满

手动恢复：
  排除原因后执行 ./scripts/native-prod-up.sh 重新启动
EOF
exit "$(( up_rc == 0 ? 1 : up_rc ))"
