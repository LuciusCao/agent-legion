#!/usr/bin/env bash
# 一键停止原生（非 Docker）生产环境：后端 (8000) 与 worker (8787)。
# SIGTERM 优雅停机（worker 有 shutdown_grace_seconds 预算用于上报在途结果），
# 超时未退出才警告提示人工处理。幂等：目标监听不存在则跳过。
#
# 进程按「绑定地址 + 端口」定位（NATIVE_BACKEND_BIND / NATIVE_WORKER_BIND，
# 默认 127.0.0.1，与 native-prod-up.sh 同一组变量）：同端口不同地址可并存
# 监听，按端口 head -1 会杀错进程；up 用什么 bind 起的，down 就用同一个
# bind 停。通配监听（*:port / [::]:port）占满整个端口，同样匹配。
set -euo pipefail

BACKEND_PORT="${NATIVE_BACKEND_PORT:-8000}"
WORKER_PORT="${NATIVE_WORKER_PORT:-8787}"
BACKEND_BIND="${NATIVE_BACKEND_BIND:-127.0.0.1}"
WORKER_BIND="${NATIVE_WORKER_BIND:-127.0.0.1}"

listener_display() {
    local host="$1"
    case "$host" in
        0.0.0.0 | ::) host="*" ;;
        *)
            if [[ "$host" != \[* ]] && [[ "$host" == *:* ]]; then
                host="[$host]"
            fi
            ;;
    esac
    echo "$host"
}

# 输出匹配「display:port 或任一通配」的全部监听 pid（去重）。
listener_pids() {
    local display port
    display="$(listener_display "$1")"
    port="$2"
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -F pn 2>/dev/null | awk \
        -v target="${display}:${port}" -v wild="*:${port}" -v wild6="[::]:${port}" '
        /^p/ { pid = substr($0, 2) }
        /^n/ {
            name = substr($0, 2)
            if (name == target || name == wild || name == wild6) print pid
        }' | sort -u
}

stop_port() {
    local bind="$1" port="$2" name="$3" grace="$4"
    local pid
    pid="$(listener_pids "$bind" "$port" | head -1 || true)"
    if [[ -z "$pid" ]]; then
        echo "$name $bind:$port 未在运行，跳过"
        return 0
    fi
    echo "停止 $name $bind:$port (pid $pid) …"
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
stop_port "$WORKER_BIND" "$WORKER_PORT" "Worker" 35 || rc=1
stop_port "$BACKEND_BIND" "$BACKEND_PORT" "后端" 15 || rc=1
exit "$rc"
