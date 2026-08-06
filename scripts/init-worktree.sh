#!/usr/bin/env bash
# 初始化新 git worktree 的开发环境（幂等，可重复执行）：
#   0. 嵌套防护：worktree 必须是主仓库根的平级子目录，嵌套直接报错
#   1. 从基准 worktree 复制 .env（若本 worktree 缺失）
#   2. 把 AGENT_LEGION_DATABASE_URL 指向按 worktree 名派生的专属 Postgres 库并尝试建库
#   3. 生成缺失的 deploy/secrets（agent_worker_register_token / vault_master_key）
# 用法: scripts/init-worktree.sh [基准 worktree 路径]（默认取 git worktree list 的第一个）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 0. 嵌套防护（AGENTS.md §1）：worktree 一律是主仓库根的平级子目录，
#    嵌套会让 data/、测试库派生与清理路径全部混乱，直接拒绝。
MAIN="$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
if [[ "$ROOT" != "$MAIN" && "$(dirname "$ROOT")" != "$MAIN/.worktrees" ]]; then
    echo "错误: worktree 禁止嵌套（当前: $ROOT）。" >&2
    echo "请先 cd 到主仓库根（$MAIN），再 git worktree add .worktrees/<name> -b <branch> <base>。" >&2
    exit 1
fi

BASE="${1:-}"
if [[ -z "$BASE" ]]; then
    BASE="$MAIN"
fi
if [[ "$BASE" == "$ROOT" ]]; then
    echo "当前就是基准 worktree，无需初始化。" >&2
    exit 0
fi

# 1. .env
if [[ ! -f .env ]]; then
    if [[ -f "$BASE/.env" ]]; then
        cp "$BASE/.env" .env
        echo "已复制 .env <- $BASE"
    else
        echo "警告: $BASE/.env 不存在，跳过 .env 复制" >&2
    fi
fi

# 2. 专属 Postgres 库
NAME="$(printf '%s' "$(basename "$ROOT")" | tr -c 'a-zA-Z0-9_' '_')"
DB="agent_legion_${NAME}"
if [[ -f .env ]]; then
    if grep -q '^AGENT_LEGION_DATABASE_URL=' .env; then
        sed -i '' -E "s|^AGENT_LEGION_DATABASE_URL=.*|AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/${DB}|" .env
    else
        echo "AGENT_LEGION_DATABASE_URL=postgresql://127.0.0.1:5432/${DB}" >> .env
    fi
    echo "AGENT_LEGION_DATABASE_URL -> ${DB}"
fi
if command -v createdb >/dev/null 2>&1; then
    createdb "$DB" 2>/dev/null && echo "已创建数据库 ${DB}" || echo "数据库 ${DB} 已存在或建库失败（如已存在可忽略）"
else
    echo "提示: 未找到 createdb，请手动创建数据库 ${DB}" >&2
fi

# 3. deploy/secrets
mkdir -p deploy/secrets
if [[ ! -s deploy/secrets/agent_worker_register_token ]]; then
    openssl rand -hex 32 > deploy/secrets/agent_worker_register_token
    echo "已生成 deploy/secrets/agent_worker_register_token"
fi
if [[ ! -s deploy/secrets/vault_master_key ]]; then
    UV_CACHE_DIR=.uv-cache uv run python -c \
        "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
        > deploy/secrets/vault_master_key
    echo "已生成 deploy/secrets/vault_master_key"
fi
chmod 600 deploy/secrets/agent_worker_register_token deploy/secrets/vault_master_key

cat <<EOF
完成。剩余手工步骤（如未做过）：
  - frontend: cd frontend && npm ci
  - 质量门: ./scripts/check-quick.sh
EOF
