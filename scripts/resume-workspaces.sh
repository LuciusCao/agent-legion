#!/usr/bin/env bash
# 按需恢复本 worktree 全部 workspace 的调度（后端每次启动都把全部 workspace
# 重置为暂停，刻意设计，防失控自跑；unknown workspace 也默认暂停）。
# 只在后端已首次启动建表并 seed 过 workspaces 之后才能生效；未建表时以
# 退出码 1 失败并提示。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 数据库 URL：优先取 .env 里的 AGENT_LEGION_DATABASE_URL（init-worktree.sh
# 已写入按 worktree 名派生的专属库），缺失时按同一规则现推。
DB_URL="$(grep -E '^(export )?AGENT_LEGION_DATABASE_URL=' .env 2>/dev/null | tail -1 | sed -E 's/^(export )?AGENT_LEGION_DATABASE_URL=//' || true)"
if [[ -z "$DB_URL" ]]; then
    NAME="$(printf '%s' "$(basename "$ROOT")" | tr -c 'a-zA-Z0-9_' '_')"
    DB_URL="postgresql://127.0.0.1:5432/agent_legion_${NAME}"
fi

# 显式传入本 worktree 的专属 URL：load_settings() 以 override=False 加载 .env，
# 调用 shell 若已导出 AGENT_LEGION_DATABASE_URL（指向基准/生产实例）会盖过
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
    echo "错误: workspace 恢复失败（后端可能尚未首次启动建表）。" >&2
    echo "      请先启动一次后端再重跑本脚本，或在控制台手动恢复。" >&2
    exit 1
fi
