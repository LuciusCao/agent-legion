#!/usr/bin/env bash
# 一键停止原生（非 Docker）生产环境：后端 (8000) 与 worker (8787)。
# SIGTERM 优雅停机（worker 有 shutdown_grace_seconds 预算用于上报在途结果），
# 超时未退出才警告提示人工处理。幂等：目标监听不存在则跳过。
#
# 进程按「绑定地址 + 端口」定位（NATIVE_BACKEND_BIND / NATIVE_WORKER_BIND，
# 默认 127.0.0.1，与 native-prod-up.sh 同一组变量）：同端口不同地址可并存
# 监听，按端口 head -1 会杀错进程；up 用什么 bind 起的，down 就用同一个
# bind 停。同族通配监听（*:port / [::]:port）占满所属族，同样匹配。
# 端口/bind 与 up 同一两级来源：进程环境 > 根 .env（#486，写进 .env 的
# bind 不必每次 down 时再 export 一遍）。
# #894：运行态优先——prod-up 落的 data/native-prod.state 记着实例实际的
# PID 与 bind/port，down 先按它停（经签名 + 工作目录校验防 PID 复用），
# 记录缺失/陈旧才回落上面这组配置定位并提示。改 bind/port 的正确顺序
# 仍是「先 down、再改配置、再 up」。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=dotenv-lib.sh
source "$ROOT/scripts/dotenv-lib.sh"
# shellcheck source=native-prod-state-lib.sh
source "$ROOT/scripts/native-prod-state-lib.sh"
STATE_FILE="$ROOT/$NATIVE_STATE_REL"

BACKEND_PORT="$(dotenv_lookup_or NATIVE_BACKEND_PORT 8000 "$ROOT/.env")"
WORKER_PORT="$(dotenv_lookup_or NATIVE_WORKER_PORT 8787 "$ROOT/.env")"
BACKEND_BIND="$(dotenv_lookup_or NATIVE_BACKEND_BIND 127.0.0.1 "$ROOT/.env")"
WORKER_BIND="$(dotenv_lookup_or NATIVE_WORKER_BIND 127.0.0.1 "$ROOT/.env")"

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

listener_family() {
    case "$1" in
        *:*) echo "6" ;;
        *) echo "4" ;;
    esac
}

# 输出匹配「display:port 或同族通配（*:port / [::]:port）」的全部监听
# pid（去重）。族别经 lsof -i4/-i6 过滤：IPv4 目标不得命中仅 IPv6 的
# 通配监听（bindv6only=1 时两类通配可在同端口并存），反之亦然。
listener_pids() {
    local display port family
    display="$(listener_display "$1")"
    port="$2"
    family="$(listener_family "$1")"
    lsof -nP -a -iTCP:"$port" -i"$family" -sTCP:LISTEN -F pn 2>/dev/null | awk \
        -v target="${display}:${port}" -v wild="*:${port}" -v wild6="[::]:${port}" '
        /^p/ { pid = substr($0, 2) }
        /^n/ {
            name = substr($0, 2)
            if (name == target || name == wild || name == wild6) print pid
        }' | sort -u
}

# 向 pid 发 SIGTERM 并在 grace 秒内等待退出。
stop_pid() {
    local pid="$1" label="$2" name="$3" grace="$4"
    echo "停止 $name $label (pid $pid) …"
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

# 按配置的 bind/port 定位（无运行态记录或记录陈旧时的回落路径）。
stop_port() {
    local bind="$1" port="$2" name="$3" grace="$4"
    local pid
    pid="$(listener_pids "$bind" "$port" | head -1 || true)"
    if [[ -z "$pid" ]]; then
        echo "$name $bind:$port 未在运行，跳过"
        return 0
    fi
    stop_pid "$pid" "$bind:$port" "$name" "$grace"
}

# 运行态优先：记录中的实例仍在（签名校验通过）就按记录停，否则回落配置。
stop_service() {
    local kind="$1" prefix="$2" bind="$3" port="$4" name="$5" grace="$6"
    local pid rec_bind rec_port
    rec_port="$(native_state_get "$STATE_FILE" "${prefix}_PORT")"
    if [[ -n "$rec_port" ]]; then
        rec_bind="$(native_state_get "$STATE_FILE" "${prefix}_BIND")"
        pid="$(native_state_live_pid "$ROOT" "$kind" "$prefix")"
        if [[ -n "$pid" ]]; then
            if [[ "$rec_bind:$rec_port" != "$bind:$port" ]]; then
                echo "提示：按运行态记录停止 ${name}（实际 ${rec_bind}:${rec_port}，当前配置为 ${bind}:${port}）"
            fi
            stop_pid "$pid" "$rec_bind:$rec_port" "$name" "$grace"
            return
        fi
        echo "提示：运行态记录中的 ${name}（${rec_bind}:${rec_port}）已不在运行或已非本实例，回落按当前配置 ${bind}:${port} 定位"
    fi
    stop_port "$bind" "$port" "$name" "$grace"
}

if [[ ! -f "$STATE_FILE" ]]; then
    echo "提示：无运行态记录（${NATIVE_STATE_REL}），按当前配置定位实例"
fi
rc=0
# 先停 worker（停止领新任务并给它上报预算），再停后端
stop_service worker WORKER "$WORKER_BIND" "$WORKER_PORT" "Worker" 35 || rc=1
stop_service backend BACKEND "$BACKEND_BIND" "$BACKEND_PORT" "后端" 15 || rc=1
# 两个服务都已确认停下才删记录；有残留时保留，供下次 down 继续按它定位。
if [[ "$rc" -eq 0 ]]; then
    rm -f "$STATE_FILE"
fi
exit "$rc"
