#!/usr/bin/env bash
# 一键启动本地生产 stack（PostgreSQL + Host + Worker）：
#   1. 检查 deploy/secrets 齐全
#   2. 起 postgres 并等待 healthy
#   3. ASR 模型（SenseVoiceSmall）缺失时在容器内预热（host 启动自检 fail-closed，必须先预热）
#   4. 构建并起全 stack，等待 host / worker healthy
# 本机覆盖文件 deploy/compose.local.yaml 存在时自动并入（bind-mount / 安全选项等）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f deploy/compose.host.yaml)
if [[ -f deploy/compose.local.yaml ]]; then
    COMPOSE+=(-f deploy/compose.local.yaml)
fi

# 1. secrets
missing=0
for f in postgres_password postgres_pgpass agent_worker_register_token; do
    if [[ ! -s "deploy/secrets/$f" ]]; then
        echo "缺少 deploy/secrets/$f（生成方式见 docs/agent-worker-deployment.md §1）" >&2
        missing=1
    fi
done
[[ "$missing" -eq 0 ]] || exit 1

# 2. postgres
"${COMPOSE[@]}" up -d postgres
echo "等待 postgres healthy…"
for i in $(seq 1 30); do
    status="$("${COMPOSE[@]}" ps --format '{{.Status}}' postgres 2>/dev/null || true)"
    [[ "$status" == *healthy* ]] && break
    sleep 2
done

# 3. ASR 模型预热（volume 已有所需模型则跳过）
MODEL_DIR=/root/.cache/modelscope/hub/models/iic/SenseVoiceSmall
if "${COMPOSE[@]}" run --rm --no-deps host test -d "$MODEL_DIR" 2>/dev/null; then
    echo "ASR 模型已就绪，跳过预热"
else
    echo "预热 ASR 模型（首次约 1-4G 下载）…"
    "${COMPOSE[@]}" run --rm --no-deps host python -c \
        "from funasr import AutoModel; AutoModel(model='iic/SenseVoiceSmall', disable_update=True)"
fi

# 4. 全 stack
"${COMPOSE[@]}" up -d --build
echo "等待 host / worker healthy…"
for i in $(seq 1 60); do
    statuses="$("${COMPOSE[@]}" ps --format '{{.Service}} {{.Status}}' 2>/dev/null || true)"
    if ! grep -qE 'starting|unhealthy|Restarting' <<<"$statuses" && grep -q 'host.*healthy' <<<"$statuses" && grep -q 'worker.*healthy' <<<"$statuses"; then
        echo "全部服务已就绪："
        "${COMPOSE[@]}" ps
        exit 0
    fi
    sleep 5
done
echo "服务未在预期时间内就绪，当前状态：" >&2
"${COMPOSE[@]}" ps >&2
exit 1
