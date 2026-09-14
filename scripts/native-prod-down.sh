#!/usr/bin/env bash
# 一键停止原生（非 Docker）生产环境：后端 (8000) 与 worker (8787)。
# SIGTERM 优雅停机（worker 有 shutdown_grace_seconds 预算用于上报在途结果），
# 超时未退出才警告提示人工处理。幂等：目标监听不存在则跳过。
#
# 进程按「绑定地址 + 端口」定位（NATIVE_BACKEND_BIND / NATIVE_WORKER_BIND，
# 默认 127.0.0.1，与 native-prod-up.sh 同一组变量）：同端口不同地址可并存
# 监听，按端口 head -1 会杀错进程；up 用什么 bind 起的，down 就用同一个
# bind 停。同族通配监听（*:port / [::]:port）占满所属族，同样匹配。
# 端口与 bind 变量读「进程环境 > 根 .env」两级来源（与 up 一致，#486）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# dotenv 解析原语统一在 scripts/lib/dotenv.sh（#486 收敛，见该文件头注释）。
source "$ROOT/scripts/lib/dotenv.sh"

BACKEND_PORT="$(dotenv_value NATIVE_BACKEND_PORT .env)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
WORKER_PORT="$(dotenv_value NATIVE_WORKER_PORT .env)"
WORKER_PORT="${WORKER_PORT:-8787}"
BACKEND_BIND="$(dotenv_value NATIVE_BACKEND_BIND .env)"
BACKEND_BIND="${BACKEND_BIND:-127.0.0.1}"
WORKER_BIND="$(dotenv_value NATIVE_WORKER_BIND .env)"
WORKER_BIND="${WORKER_BIND:-127.0.0.1}"

# 同端口的通配形态监听（*:port / [::]:port）由 listener_pids 的通配分支
# 覆盖（bindv6only=0 时 IPv4 bind 起的进程在 lsof 里就显示为 *:port）。
# 通配 bind（0.0.0.0 / ::）请求时 listener_pids 只命中「显示为通配」的
# 进程——具体地址监听（如 127.0.0.1:8000 的旧实例）不匹配，返回空被
# 当成「未在运行」跳过、rc=0：up 侧的通配幂等兜底正是为这种残留态跳过
# 启动的（双实例防线），down 同 bind 跑一遍却「停不掉它」，用户只能
# kill。因此通配 bind 未命中任何 pid 且同端口同族仍有任意监听时改判
# 未完全停止（rc=1）并指引：这是用户按 up 的提示来重启、却停在「具体
# bind 旧实例仍在」的半途态，静默 0 会伪装成成功。恢复操作序列（.env
# 已持久携带新 bind 时）：NATIVE_BACKEND_BIND=<旧地址> make prod-down 用
# 旧地址定位停掉残留实例，再按新 bind 起（见
# docs/agent-worker-deployment.md 原生形态段）。

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

# 同端口同族是否「任意地址」有监听（不限 bind 形态）：up 的通配 bind
# 幂等兜底为取观测地址升级成了 port_first_listener_display（#486 收尾），
# 本脚本只需要布尔判定，保留同款 lsof 查询的布尔形态；up 在通配 bind
# 幂等兜底用它识别残留实例，down 在通配 bind 未命中 pid 时用它识别
# 「仍在监听但 bind 形态不同」的残留实例。
port_has_any_listener() {
    local port family
    port="$1"
    family="$2"
    lsof -nP -a -iTCP:"$port" -i"$family" -sTCP:LISTEN -F n 2>/dev/null | grep -q '^n'
}

is_wildcard_bind() {
    case "$1" in
        0.0.0.0 | ::) return 0 ;;
        *) return 1 ;;
    esac
}

stop_port() {
    local bind="$1" port="$2" name="$3" grace="$4"
    local pid
    pid="$(listener_pids "$bind" "$port" | head -1 || true)"
    if [[ -z "$pid" ]]; then
        if is_wildcard_bind "$bind" \
            && port_has_any_listener "$port" "$(listener_family "$bind")"; then
            # 变量一律花括号（多字节标点紧跟裸 $VAR 的 bash 陷阱，见
            # native-prod-up.sh 同款注释）。
            echo "警告: ${name} 端口 :${port} 仍有监听但绑定形态与 ${bind} 不同（可能是旧实例绑的具体地址）；未停止，请用具体 bind 重跑或按日志 data/logs/prod-*.log 定位进程" >&2
            return 1
        fi
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
    echo "警告：${name} (pid ${pid}) ${grace}s 内未退出，请人工检查（日志 data/logs/prod-*.log）" >&2
    return 1
}

rc=0
# 先停 worker（停止领新任务并给它上报预算），再停后端
stop_port "$WORKER_BIND" "$WORKER_PORT" "Worker" 35 || rc=1
stop_port "$BACKEND_BIND" "$BACKEND_PORT" "后端" 15 || rc=1
exit "$rc"
