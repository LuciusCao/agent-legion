#!/usr/bin/env bash
# 一键停止原生（非 Docker）生产环境：后端 (8000) 与 worker (8787)。
# SIGTERM 优雅停机（worker 有 shutdown_grace_seconds 预算用于上报在途结果），
# 超时未退出才警告提示人工处理。幂等：端口无监听则跳过。
set -euo pipefail

BACKEND_PORT="${NATIVE_BACKEND_PORT:-8000}"
WORKER_PORT="${NATIVE_WORKER_PORT:-8787}"

stop_port() {
    local port="$1" name="$2" grace="$3"
    local pid
    pid="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1 || true)"
    if [[ -z "$pid" ]]; then
        echo "$name :$port 未在运行，跳过"
        return 0
    fi
    echo "停止 $name :$port (pid $pid) …"
    kill "$pid" 2>/dev/null || true
    for i in $(seq 1 "$grace"); do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "$name 已停止"
            return 0
        fi
        sleep 1
    done
    echo "警告：$name (pid $pid) ${grace}s 内未退出，请人工检查（日志 data/logs/prod-*.log）" >&2
    return 1
}

rc=0
# 先停 worker（停止领新任务并给它上报预算），再停后端
stop_port "$WORKER_PORT" "Worker" 35 || rc=1
stop_port "$BACKEND_PORT" "后端" 15 || rc=1
exit "$rc"
