#!/usr/bin/env bash
# 初始化新 git worktree 的开发环境（幂等，可重复执行）：
#   0. 嵌套防护：worktree 必须是主仓库根的平级子目录，嵌套直接报错
#   1. 从基准 worktree 复制 .env（若本 worktree 缺失）
#   2. 把 AGENT_LEGION_DATABASE_URL 指向按 worktree 名派生的专属 Postgres 库并尝试建库
#   3. 生成缺失的 deploy/secrets（agent_worker_register_token / vault_master_key）
# 用法: scripts/init-worktree.sh [基准 worktree 路径]（默认取第一个非 bare 且非当前的 worktree）
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

# 在主仓库根本身执行时直接退出（bare 主仓库无工作区，更不会走到这里）
if [[ "$ROOT" == "$MAIN" ]]; then
    echo "当前就是主仓库根，无需初始化。" >&2
    exit 0
fi

BASE="${1:-}"
if [[ -z "$BASE" ]]; then
    # 主仓库可能是 bare（无工作区、无 .env），默认取第一个非 bare 且非当前的 worktree 作基准
    BASE="$(git worktree list --porcelain | awk -v root="$ROOT" '
        /^worktree / {
            if (wt != "" && !isbare && wt != root) { print wt; found=1; exit }
            wt = substr($0, 10); isbare = 0
        }
        /^bare$/ { isbare = 1 }
        END { if (!found && wt != "" && !isbare && wt != root) print wt }
    ')"
fi
if [[ -n "$BASE" && "$BASE" == "$ROOT" ]]; then
    echo "当前就是基准 worktree，无需初始化。" >&2
    exit 0
fi

# 1. .env
if [[ ! -f .env ]]; then
    if [[ -z "$BASE" ]]; then
        echo "警告: 未找到基准 worktree（主仓库为 bare 且无其他 worktree），跳过 .env 复制" >&2
    elif [[ -f "$BASE/.env" ]]; then
        cp "$BASE/.env" .env
        echo "已复制 .env <- $BASE"
    else
        echo "警告: $BASE/.env 不存在，跳过 .env 复制" >&2
    fi
fi

# 2. 专属 Postgres 库
NAME="$(printf '%s' "$(basename "$ROOT")" | tr -c 'a-zA-Z0-9_' '_')"
DB="agent_legion_${NAME}"
DB_URL="postgresql://127.0.0.1:5432/${DB}"
if [[ -f .env ]]; then
    if grep -q '^AGENT_LEGION_DATABASE_URL=' .env; then
        sed -i '' -E "s|^AGENT_LEGION_DATABASE_URL=.*|AGENT_LEGION_DATABASE_URL=${DB_URL}|" .env
    else
        echo "AGENT_LEGION_DATABASE_URL=${DB_URL}" >> .env
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

# 4. config/agent-worker.yaml：缺失时从基准 worktree 复制并改写本实例字段
#    （host_url 指向开发后端、worker_id 按 worktree 派生）。注意生效配置是
#    状态副本 data/agent-worker-service/worker.yaml——首次启动后改 config
#    文件不会自动生效，要走 worker 控制台或 PUT /api/config。
if [[ ! -f config/agent-worker.yaml ]]; then
    if [[ -n "$BASE" && -f "$BASE/config/agent-worker.yaml" ]]; then
        mkdir -p config
        cp "$BASE/config/agent-worker.yaml" config/agent-worker.yaml
        # host_url 指向本实例的开发后端端口（与 make dev-backend 的 DEV_BACKEND_PORT 一致），
        # register_token_file 指向本 worktree 生成的本地密钥（基准配置里的
        # /run/secrets/... 是容器路径，宿主机 make dev-worker 读不到）。
        sed -i '' -E "s|^host_url:.*|host_url: http://127.0.0.1:${DEV_BACKEND_PORT:-8001}|" config/agent-worker.yaml
        sed -i '' -E "s|^worker_id:.*|worker_id: ${NAME}|" config/agent-worker.yaml
        sed -i '' -E "s|^name:.*|name: ${NAME} (worktree)|" config/agent-worker.yaml
        sed -i '' -E "s|^register_token_file:.*|register_token_file: ${ROOT}/deploy/secrets/agent_worker_register_token|" config/agent-worker.yaml
        echo "已生成 config/agent-worker.yaml <- $BASE（host_url/worker_id/name/register_token_file 已改写）"
    else
        echo "提示: 基准 worktree 无 config/agent-worker.yaml，跳过 worker 配置种子" >&2
    fi
fi

# 5. 恢复 workspace 调度：后端每次启动都把全部 workspace 重置为暂停（刻意设计，
#    防止重启后任务不受控自跑），且 unknown workspace 默认暂停。只有后端已建表
#    并 seed 过 workspaces 时这步才能生效；否则在首次启动后端后重跑本脚本（幂等）。
if [[ -f .env ]]; then
    # 显式传入本 worktree 的专属 URL：load_settings() 以 override=False 加载 .env，
    # 调用 shell 若已导出 AGENT_LEGION_DATABASE_URL（指向基准/生产实例）会盖过新
    # .env，导致在错误数据库里恢复调度。
    if PYTHONPATH="$ROOT" AGENT_LEGION_DATABASE_URL="$DB_URL" UV_CACHE_DIR=.uv-cache uv run python - <<'PY'
from server.app.db.transaction import read_connection
from server.app.settings import load_settings
from server.app.worker_control import WorkspaceWorkerControl

settings = load_settings()
control = WorkspaceWorkerControl(settings.database_url)
with read_connection(settings.database_url) as conn:
    rows = conn.execute("select id from workspaces").fetchall()
for row in rows:
    control.resume(str(row["id"]))
    print(f"已恢复 workspace 调度: {row['id']}")
PY
    then
        :
    else
        echo "提示: workspace 恢复未执行（后端可能尚未首次启动建表），" >&2
        echo "      请在启动后端后重跑本脚本，或在控制台手动恢复。" >&2
    fi
fi

cat <<EOF
完成。剩余手工步骤（如未做过）：
  - frontend: cd frontend && npm ci
  - 质量门: ./scripts/check-quick.sh
  - worker: claim 默认关闭（刻意设计），启动后经 worker 控制台（默认 8789）
    或 PUT /api/config 打开 claim_enabled；capabilities/models 已在
    config/agent-worker.yaml 种子配置中声明，首次导入后修改要走控制台/API
EOF
