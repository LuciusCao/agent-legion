#!/usr/bin/env bash
# 一键启动原生（非 Docker）生产环境：后端 (8000) + worker (8787)。
# 前端无独立进程：后端直接服务 frontend/dist（本脚本会先构建）。
# 幂等：端口已被监听时跳过对应进程的启动。进程经 nohup + caffeinate
# 脱离终端并防睡眠，日志在 data/logs/prod-{backend,worker}.log。
# 端口与绑定地址可分别用 NATIVE_BACKEND_PORT / NATIVE_WORKER_PORT 与
# NATIVE_BACKEND_BIND / NATIVE_WORKER_BIND 覆盖（默认 8000/8787 与 127.0.0.1；
# 暴露给局域网/overlay 网络时把 bind 设为对应网卡地址，S3 联动配置见
# docs/agent-worker-deployment.md）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${NATIVE_BACKEND_PORT:-8000}"
WORKER_PORT="${NATIVE_WORKER_PORT:-8787}"
BACKEND_BIND="${NATIVE_BACKEND_BIND:-127.0.0.1}"
WORKER_BIND="${NATIVE_WORKER_BIND:-127.0.0.1}"
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

# 健康检查与就绪提示用的探测地址：0.0.0.0 / :: 的全接口监听必然含
# loopback，归一为 127.0.0.1；绑定具体网卡地址时只有该地址可达（探测
# loopback 必然失败），原样返回；IPv6 字面量补 URL 要求的方括号。
health_host() {
    local host="$1"
    case "$host" in
        0.0.0.0 | ::) host=127.0.0.1 ;;
    esac
    if [[ "$host" == *:* ]]; then
        host="[$host]"
    fi
    echo "$host"
}
BACKEND_HEALTH_HOST="$(health_host "$BACKEND_BIND")"
WORKER_HEALTH_HOST="$(health_host "$WORKER_BIND")"

# 绑定具体网卡地址时的本地接入提醒：非 loopback 绑定后，指向 127.0.0.1 的
# 既有接入不再可达——本地 Worker 状态副本的 host_url 会让它静默退避重试注册
# （不崩溃、不易察觉），本机浏览器访问 127.0.0.1:8787 控制台同理。配置一律
# 走控制台/API（#323 状态副本纪律），脚本只提示、不代改。
is_loopback() {
    case "$1" in
        127.* | ::1 | localhost) return 0 ;;
        *) return 1 ;;
    esac
}
binds_specific_interface() {
    ! is_loopback "$1" && [[ "$1" != "0.0.0.0" && "$1" != "::" ]]
}
if binds_specific_interface "$BACKEND_BIND" \
    && [[ -f data/agent-worker-service/worker.yaml ]] \
    && grep -Eq 'host_url:[[:space:]]*https?://(127\.|localhost)' data/agent-worker-service/worker.yaml; then
    echo "警告: 后端已绑定 $BACKEND_BIND，但本地 Worker 状态副本的 host_url 仍指向 loopback——请经 Worker 控制台改为 http://$BACKEND_BIND:$BACKEND_PORT，否则本地 Worker 将无法注册（静默退避重试）" >&2
fi
if binds_specific_interface "$WORKER_BIND"; then
    echo "提示: Worker 控制台已绑定 $WORKER_BIND，本机访问地址改为 http://$WORKER_HEALTH_HOST:$WORKER_PORT（127.0.0.1 不再监听）" >&2
fi

# 1.5 材料对象存储：原生形态下后端/worker 是本机进程，对象存储仍由 docker
# compose 托管（compose.host.yaml 里 seaweedfs/rustfs 各挂自己的 profile，
# 显式指定服务名时 profile 自动启用）。后端选择 AGENT_LEGION_LOCAL_S3_BACKEND
# =seaweedfs|rustfs（默认 seaweedfs）；是否启动由 AGENT_LEGION_LOCAL_S3=
# auto|always|never（默认 auto）三态开关决策，判断逻辑见
# scripts/local-s3-decide.sh（auto：endpoint 指向本机或未配置 S3 → 启动；
# endpoint 远程或只配 bucket/凭据 → 跳过并输出原因）。幂等：已在运行则
# no-op。docker 不可用或启动失败仅告警——未配置/未就绪 S3 时材料 API
# 降级为 503，其余功能不受影响。
LOCAL_S3_DECISION="skip"
local_s3_rc=0
LOCAL_S3_DECISION="$(scripts/local-s3-decide.sh .env deploy/.env)" || local_s3_rc=$?
if [[ "$local_s3_rc" -eq 2 ]]; then
    exit 2  # 开关值非法是配置错误，fail fast（原因已由脚本写到 stderr）
fi
# 后端分派的服务名（seaweedfs/rustfs）由 decide 脚本统一解析，避免这里
# 再写一份 dotenv 解析。
LOCAL_S3_SERVICE="$(scripts/local-s3-decide.sh --service-name .env deploy/.env)"
if [[ "$LOCAL_S3_DECISION" == "start" ]]; then
    if command -v docker >/dev/null 2>&1; then
        COMPOSE_FILES=(-f deploy/compose.host.yaml)
        [[ -f deploy/compose.local.yaml ]] && COMPOSE_FILES+=(-f deploy/compose.local.yaml)
        if docker compose "${COMPOSE_FILES[@]}" up -d "$LOCAL_S3_SERVICE" >/dev/null 2>&1; then
            echo "${LOCAL_S3_SERVICE}（材料对象存储）已就绪"
        else
            echo "警告: ${LOCAL_S3_SERVICE} 启动失败，材料相关功能将不可用（详见 deploy 文档）" >&2
        fi
    else
        echo "提示: 未检测到 docker，跳过 ${LOCAL_S3_SERVICE} 启动；如需材料功能请自行启动 S3 兼容存储" >&2
    fi
elif [[ "$local_s3_rc" -ne 0 ]]; then
    # 决策为 start 但凭据未配齐：原生形态降级为告警（与 docker 不可用同级），
    # 不阻断后端启动。
    echo "警告: 跳过本地 ${LOCAL_S3_SERVICE} 启动（原因见上方），材料相关功能将不可用" >&2
fi

# 2. 后端
if port_listening "$BACKEND_PORT"; then
    echo "后端已在 :$BACKEND_PORT 运行，跳过"
else
    echo "启动后端 $BACKEND_BIND:$BACKEND_PORT …"
    ulimit -n 65535
    # 共享库 schema 门（server/app/db/schema.py）：prod 是有意迁移裸
    # agent_legion 库的操作者，显式授予 opt-in；误连该库的工具脚本
    # （缺 .env 的 worktree export_openapi 等）则被硬拦。
    AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1 \
    nohup ${CAFFEINATE:+$CAFFEINATE -is} .venv/bin/python -m uvicorn \
        server.app.main:create_prod_app --factory --host "$BACKEND_BIND" --port "$BACKEND_PORT" \
        --timeout-graceful-shutdown 3 \
        > data/logs/prod-backend.log 2>&1 &
fi

# 3. Worker
if port_listening "$WORKER_PORT"; then
    echo "Worker 已在 :$WORKER_PORT 运行，跳过"
else
    echo "启动 Worker $WORKER_BIND:$WORKER_PORT …"
    ulimit -n 65535
    nohup ${CAFFEINATE:+$CAFFEINATE -is} .venv/bin/python -m worker.service \
        --state-dir data/agent-worker-service \
        --host "$WORKER_BIND" --port "$WORKER_PORT" \
        > data/logs/prod-worker.log 2>&1 &
fi

# 4. 健康等待：最多 5 分钟（#127——冷启动时 PG 冷缓存、schema 引导等
# 仍可能超过 1 分钟；等待期间每 30s 输出一次进度，避免误报启动失败）。
for i in $(seq 1 150); do
    backend_ok=false; worker_ok=false
    curl -sS -m 2 "http://$BACKEND_HEALTH_HOST:$BACKEND_PORT/api/health" >/dev/null 2>&1 && backend_ok=true
    curl -sS -m 2 "http://$WORKER_HEALTH_HOST:$WORKER_PORT/api/health" >/dev/null 2>&1 && worker_ok=true
    if $backend_ok && $worker_ok; then
        echo "原生环境已就绪：后端 http://$BACKEND_HEALTH_HOST:$BACKEND_PORT （含前端 SPA），Worker 控制台 http://$WORKER_HEALTH_HOST:$WORKER_PORT"
        exit 0
    fi
    if (( i % 15 == 0 )); then
        echo "等待就绪中（已 $((i * 2))s）：backend_ok=$backend_ok worker_ok=$worker_ok"
    fi
    sleep 2
done
echo "服务未在预期时间内就绪，日志见 data/logs/prod-{backend,worker}.log" >&2
exit 1
