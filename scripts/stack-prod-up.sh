#!/usr/bin/env bash
# 一键启动本地生产 stack（PostgreSQL + Host + Worker）：
#   1. 检查 deploy/secrets 齐全
#   2. 起 postgres 并等待 healthy
#   3. 构建并起全 stack，等待 host / worker healthy
# 本机覆盖文件 deploy/compose.local.yaml 存在时自动并入（bind-mount / 安全选项等）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f deploy/compose.host.yaml)
if [[ -f deploy/compose.local.yaml ]]; then
    COMPOSE+=(-f deploy/compose.local.yaml)
fi

# 本地 RustFS 三态开关（AGENT_LEGION_LOCAL_S3=auto|always|never，默认 auto；
# 判断逻辑见 scripts/local-s3-decide.sh）。compose 插值只读 deploy/.env，
# 且 host 的 endpoint 默认注入 http://rustfs:9000（--default-endpoint 如实
# 反映该注入）；决策为 start 时经 --profile materials-local 启用 rustfs
# 服务。决策失败（开关值非法 / 决策启动但凭据未配齐）fail fast，原因由
# 脚本写到 stderr——与旧版 compose 的 :? 强校验同级。
if [[ "$(scripts/local-s3-decide.sh --default-endpoint http://rustfs:9000 deploy/.env)" == "start" ]]; then
    COMPOSE+=(--profile materials-local)
fi

# 1. secrets（worker 注册走 scoped token（Host UI 签发），不再需要全局 secret）
missing=0
for f in postgres_password postgres_pgpass vault_master_key; do
    if [[ ! -s "deploy/secrets/$f" ]]; then
        echo "缺少 deploy/secrets/${f}（生成方式见 docs/agent-worker-deployment.md §1）" >&2
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
[[ "$status" == *healthy* ]] || { echo "postgres 未在预期时间内 healthy（当前: ${status:-unknown}）" >&2; exit 1; }

# 3. 全 stack
# （ASR 模型预热步骤随 funasr/ASR 渠道一并退役：torch/CUDA 依赖链已从镜像移除）
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
