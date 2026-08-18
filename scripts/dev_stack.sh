#!/usr/bin/env bash
# 开发环境一键启停：backend (uvicorn --reload) + frontend (vite) + worker service，
# 由 make dev-up / dev-down / dev-status 调用；启动命令复用 Makefile 的
# dev-backend / dev-frontend / dev-worker target（端口变量经环境透传）。
# 幂等：端口已被监听视为该组件在运行，up 跳过启动、down 只停监听中的组件。
# 进程经 nohup 脱离终端，日志在 data/logs/dev-{backend,frontend,worker}.log。
# 多 worktree 隔离靠 DEV_BACKEND_PORT / DEV_FRONTEND_PORT / AGENT_WORKER_UI_PORT。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${DEV_BACKEND_PORT:-8001}"
FRONTEND_PORT="${DEV_FRONTEND_PORT:-5174}"
WORKER_PORT="${AGENT_WORKER_UI_PORT:-8789}"
LOG_DIR="data/logs"

port_listening() {
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

http_ok() {
    curl -sS -m 2 -o /dev/null "http://127.0.0.1:$1$2" 2>/dev/null
}

print_summary() {
    cat <<EOF
开发环境已就绪：
  后端 API      http://127.0.0.1:${BACKEND_PORT}
  前端控制台    http://127.0.0.1:${FRONTEND_PORT}
  Worker 控制台 http://127.0.0.1:${WORKER_PORT}
  日志：${LOG_DIR}/dev-{backend,frontend,worker}.log
  停止：make dev-down；状态：make dev-status
EOF
}

start_component() {
    local name="$1" port="$2" target="$3" log="$4"
    if port_listening "$port"; then
        echo "${name}已在 :$port 运行，跳过"
    else
        echo "启动${name} :$port …（日志 ${log}）"
        nohup make "$target" > "$log" 2>&1 &
    fi
}

cmd_up() {
    mkdir -p "$LOG_DIR"
    if [[ ! -d frontend/node_modules ]]; then
        echo "安装前端依赖…"
        (cd frontend && npm ci)
    fi

    start_component "后端" "$BACKEND_PORT" dev-backend "$LOG_DIR/dev-backend.log"
    start_component "前端" "$FRONTEND_PORT" dev-frontend "$LOG_DIR/dev-frontend.log"
    if [[ -f config/agent-worker.yaml ]]; then
        start_component "Worker" "$WORKER_PORT" dev-worker "$LOG_DIR/dev-worker.log"
    else
        echo "缺少 config/agent-worker.yaml（先跑 scripts/init-worktree.sh 种子），跳过 Worker" >&2
    fi

    for _ in $(seq 1 45); do
        local ok=true
        http_ok "$BACKEND_PORT" /api/health || ok=false
        http_ok "$FRONTEND_PORT" / || ok=false
        if [[ -f config/agent-worker.yaml ]]; then
            http_ok "$WORKER_PORT" /api/health || ok=false
        fi
        if $ok; then
            print_summary
            return 0
        fi
        sleep 2
    done
    echo "有服务未在预期时间内就绪，日志见 $LOG_DIR/dev-*.log" >&2
    return 1
}

stop_port() {
    local port="$1" name="$2" grace="$3"
    local pid
    pid="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1 || true)"
    if [[ -z "$pid" ]]; then
        echo "$name :$port 未在运行，跳过"
        return 0
    fi
    # 监听端口的是子进程（uvicorn --reload 的 server、npm 拉起的 vite）：
    # 只杀子进程父进程会把它重新拉起，父（reloader / npm）要一起 SIGTERM。
    local ppid
    ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    echo "停止 $name :$port (pid $pid) …"
    if [[ -n "$ppid" && "$ppid" != "1" ]]; then
        kill "$ppid" 2>/dev/null || true
    fi
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 "$grace"); do
        if ! port_listening "$port"; then
            echo "$name 已停止"
            return 0
        fi
        sleep 1
    done
    echo "警告：$name :$port ${grace}s 内仍在监听，请人工检查（日志 $LOG_DIR/dev-*.log）" >&2
    return 1
}

cmd_down() {
    local rc=0
    # 先停 Worker（停止领新任务并上报在途结果），再停后端与前端
    stop_port "$WORKER_PORT" "Worker" 35 || rc=1
    stop_port "$BACKEND_PORT" "后端" 15 || rc=1
    stop_port "$FRONTEND_PORT" "前端" 10 || rc=1
    return "$rc"
}

status_line() {
    local name="$1" port="$2" url="$3"
    if port_listening "$port"; then
        echo "  [运行中] $name  $url"
    else
        echo "  [未运行] $name  $url"
    fi
}

cmd_status() {
    echo "开发环境状态："
    status_line "后端 API      " "$BACKEND_PORT" "http://127.0.0.1:$BACKEND_PORT"
    status_line "前端控制台    " "$FRONTEND_PORT" "http://127.0.0.1:$FRONTEND_PORT"
    status_line "Worker 控制台 " "$WORKER_PORT" "http://127.0.0.1:$WORKER_PORT"
    echo "  日志：$LOG_DIR/dev-{backend,frontend,worker}.log"
}

case "${1:-}" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    *)
        echo "用法: $0 {up|down|status}" >&2
        exit 2
        ;;
esac
