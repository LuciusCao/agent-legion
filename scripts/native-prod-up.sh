#!/usr/bin/env bash
# 一键启动原生（非 Docker）生产环境：后端 (8000) + worker (8787)。
# 前端无独立进程：后端直接服务 frontend/dist（本脚本会先构建）。
# 幂等：端口已被监听时跳过对应进程的启动。进程经 nohup + caffeinate
# 脱离终端并防睡眠，日志在 data/logs/prod-{backend,worker}.log。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${NATIVE_BACKEND_PORT:-8000}"
WORKER_PORT="${NATIVE_WORKER_PORT:-8787}"
CAFFEINATE="$(command -v caffeinate || true)"

mkdir -p data/logs

# 1. 依赖与前端构建
if [[ ! -d frontend/node_modules ]]; then
    echo "安装前端依赖…"
    (cd frontend && npm ci)
fi
echo "构建前端…"
(cd frontend && npm run build)
echo "同步 Python 依赖…"
UV_CACHE_DIR=.uv-cache uv sync --frozen
echo "检测 velites 二进制新鲜度…"
./scripts/ensure-velites.sh

port_listening() {
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

# 2. 后端
if port_listening "$BACKEND_PORT"; then
    echo "后端已在 :$BACKEND_PORT 运行，跳过"
else
    echo "启动后端 :$BACKEND_PORT …"
    ulimit -n 65535
    nohup ${CAFFEINATE:+$CAFFEINATE -is} .venv/bin/python -m uvicorn \
        server.app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" \
        --timeout-graceful-shutdown 3 \
        > data/logs/prod-backend.log 2>&1 &
fi

# 3. Worker
if port_listening "$WORKER_PORT"; then
    echo "Worker 已在 :$WORKER_PORT 运行，跳过"
else
    echo "启动 Worker :$WORKER_PORT …"
    ulimit -n 65535
    nohup ${CAFFEINATE:+$CAFFEINATE -is} .venv/bin/python -m worker.service \
        --config config/agent-worker.yaml \
        --state-dir data/agent-worker-service \
        --host 127.0.0.1 --port "$WORKER_PORT" \
        > data/logs/prod-worker.log 2>&1 &
fi

# 4. 健康等待：最多 5 分钟（#127——冷启动时 PG 冷缓存、schema 引导等
# 仍可能超过 1 分钟；等待期间每 30s 输出一次进度，避免误报启动失败）。
for i in $(seq 1 150); do
    backend_ok=false; worker_ok=false
    curl -sS -m 2 "http://127.0.0.1:$BACKEND_PORT/api/health" >/dev/null 2>&1 && backend_ok=true
    curl -sS -m 2 "http://127.0.0.1:$WORKER_PORT/api/health" >/dev/null 2>&1 && worker_ok=true
    if $backend_ok && $worker_ok; then
        echo "原生环境已就绪：后端 http://127.0.0.1:$BACKEND_PORT （含前端 SPA），Worker 控制台 http://127.0.0.1:$WORKER_PORT"
        exit 0
    fi
    if (( i % 15 == 0 )); then
        echo "等待就绪中（已 $((i * 2))s）：backend_ok=$backend_ok worker_ok=$worker_ok"
    fi
    sleep 2
done
echo "服务未在预期时间内就绪，日志见 data/logs/prod-{backend,worker}.log" >&2
exit 1
