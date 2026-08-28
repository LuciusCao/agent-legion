#!/usr/bin/env bash
# 初始化新 git worktree 的开发环境（幂等，可重复执行）：
#   0. 嵌套防护：worktree 必须是主仓库根的平级子目录，嵌套直接报错
#   1. 从基准 worktree 复制 .env（若本 worktree 缺失；无法复制则 fail-fast——
#      缺 .env 会让后端回落共享默认库/prod）
#   2. 把 AGENT_LEGION_DATABASE_URL 指向按 worktree 名派生的专属 Postgres 库并尝试建库
#   2.5 按 worktree 名派生 AGENT_LEGION_S3_BUCKET 写入 .env，endpoint 可达时
#       建 bucket 并配置浏览器直传所需的前端 dev origin CORS
#   3. 生成缺失的 deploy/secrets（vault_master_key；worker 全局注册 token 已退役，见 issue #35）
# 用法: scripts/init-worktree.sh [基准 worktree 路径]（默认取第一个非 bare 且非当前的 worktree）
set -euo pipefail

# BSD/GNU sed 对 `-i` 的参数语法不同；带显式 backup suffix 的形式两边
# 都支持。替换成功后删除备份，避免开发配置目录残留 `.bak`。
replace_in_place() {
    local expression="$1"
    local path="$2"
    sed -i.bak -E "$expression" "$path"
    rm -f "${path}.bak"
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 0. 嵌套防护（AGENTS.md §1）：worktree 一律是主仓库根的平级子目录，
#    嵌套会让 data/、测试库派生与清理路径全部混乱，直接拒绝。
MAIN="$(git worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
if [[ "$ROOT" != "$MAIN" && "$(dirname "$ROOT")" != "$MAIN/.worktrees" ]]; then
    echo "错误: worktree 禁止嵌套（当前: ${ROOT}）。" >&2
    echo "请先 cd 到主仓库根（${MAIN}），再 git worktree add .worktrees/<name> -b <branch> <base>。" >&2
    exit 1
fi

# 在主仓库根本身执行时直接退出（bare 主仓库无工作区，更不会走到这里）
if [[ "$ROOT" == "$MAIN" ]]; then
    echo "当前就是主仓库根，无需初始化。" >&2
    exit 0
fi

BASE="${1:-}"
if [[ -z "$BASE" ]]; then
    # 默认取第一个非 bare、非当前、非主仓库根的 worktree 作基准（主仓库根是
    # bare/无工作区配置，永不适合作基准）。注意选中的基准自身也可能缺 .env，
    # 由下方 fail-fast 兜底（2026-08-18 事故：基准 worktree 缺 .env → 新
    # worktree 无 .env → 后端回落共享默认库即 prod 库）。
    BASE="$(git worktree list --porcelain | awk -v root="$ROOT" -v main="$MAIN" '
        /^worktree / {
            if (wt != "" && !isbare && wt != root && wt != main) { print wt; found=1; exit }
            wt = substr($0, 10); isbare = 0
        }
        /^bare$/ { isbare = 1 }
        END { if (!found && wt != "" && !isbare && wt != root && wt != main) print wt }
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

# .env 缺失是硬错误：没有它，后端/门禁会回落代码默认的共享库（即 prod 库），
# 启动迁移直接改写 prod（2026-08-18 事故的另一半根因）。fail-fast，不留
# 「警告然后继续」的余地。
if [[ ! -f .env ]]; then
    echo "错误: .env 缺失且无法从基准 worktree 复制（BASE=${BASE:-未找到}）。" >&2
    echo "缺少 .env 时后端会回落共享默认库（prod）——请手工从其他 worktree 复制 .env 后重跑本脚本。" >&2
    exit 1
fi

# 2. 专属 Postgres 库
NAME="$(printf '%s' "$(basename "$ROOT")" | tr -c 'a-zA-Z0-9_' '_')"
DB="agent_legion_${NAME}"
DB_URL="postgresql://127.0.0.1:5432/${DB}"
if [[ -f .env ]]; then
    if grep -qE '^(export )?AGENT_LEGION_DATABASE_URL=' .env; then
        replace_in_place "s|^(export )?AGENT_LEGION_DATABASE_URL=.*|\1AGENT_LEGION_DATABASE_URL=${DB_URL}|" .env
    else
        echo "AGENT_LEGION_DATABASE_URL=${DB_URL}" >> .env
    fi
    echo "AGENT_LEGION_DATABASE_URL -> ${DB}"
fi
if command -v createdb >/dev/null 2>&1; then
    createdb "$DB" 2>/dev/null && echo "已创建数据库 ${DB}" || echo "数据库 ${DB} 已存在或建库失败（如已存在可忽略）"
    # role 隔离护栏（scripts/drop-worktree-db.sh）：派生库属主对齐
    # agent_legion_dev（集群存在该 role 时），清理路径走非 superuser
    # role，对共享/prod 库物理不可 drop；best-effort，失败仅 warning。
    if command -v psql >/dev/null 2>&1 \
        && psql -d postgres -tAc "select 1 from pg_roles where rolname='agent_legion_dev'" 2>/dev/null | grep -q 1; then
        psql -d postgres -qc "ALTER DATABASE \"$DB\" OWNER TO agent_legion_dev" 2>/dev/null \
            && echo "数据库 ${DB} 属主已对齐 agent_legion_dev" \
            || echo "提示: ${DB} 属主对齐 agent_legion_dev 失败（可手动 ALTER DATABASE OWNER）" >&2
    fi
else
    echo "提示: 未找到 createdb，请手动创建数据库 ${DB}" >&2
fi

# 2.5 专属 S3 bucket（材料存储，materials-and-runs 设计 §6.3）：开发机共享一个
#     RustFS 实例，bucket 按 worktree 名派生并无条件改写 .env（与专属
#     Postgres 库同一模式）；endpoint 与凭据随 .env 整体从基准 worktree 继承。
#     endpoint 不可达时跳过建 bucket / 配 CORS（warning，不 fail——离线/CI
#     场景），此后材料 API 仅降级为 503。
BUCKET="agent-legion-$(printf '%s' "$(basename "$ROOT")" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9-' '-')"
# 无条件改写为派生值（与上方 DATABASE_URL 块同一模式）：.env 是从基准
# worktree 复制的，本就带着基准的 bucket，「保留原值」会让所有派生
# worktree 共享基准 bucket，违背 per-worktree 隔离。
if grep -qE '^(export )?AGENT_LEGION_S3_BUCKET=' .env; then
    replace_in_place "s|^(export )?AGENT_LEGION_S3_BUCKET=.*|\1AGENT_LEGION_S3_BUCKET=${BUCKET}|" .env
else
    echo "AGENT_LEGION_S3_BUCKET=${BUCKET}" >> .env
fi
echo "AGENT_LEGION_S3_BUCKET -> ${BUCKET}"
# 建 bucket：逻辑抽在 scripts/ensure-s3-bucket.py（与 dev_stack.sh 共用），
# 复用 server/app/storage 的 env 加载（.env 经 load_dotenv 生效，
# override=False——调用 shell 已导出的同名变量优先）。任何失败（endpoint
# 不可达、boto3 缺失、凭据错误）都降级为提示，不阻断初始化。
if PYTHONPATH="$ROOT" UV_CACHE_DIR=.uv-cache uv run python scripts/ensure-s3-bucket.py .env; then
    :
else
    echo "提示: S3 endpoint 不可达或未配置，跳过建 bucket（材料 API 将降级为 503；" >&2
    echo "      启动共享 RustFS 后重跑本脚本即可补齐）。" >&2
fi

# 3. deploy/secrets
mkdir -p deploy/secrets
if [[ ! -s deploy/secrets/vault_master_key ]]; then
    UV_CACHE_DIR=.uv-cache uv run python -c \
        "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
        > deploy/secrets/vault_master_key
    echo "已生成 deploy/secrets/vault_master_key"
fi
chmod 600 deploy/secrets/vault_master_key

# 4. config/agent-worker.yaml：缺失时从基准 worktree 复制并改写本实例字段
#    （host_url 指向开发后端、worker_id 按 worktree 派生）。注意生效配置是
#    状态副本 data/agent-worker-service/worker.yaml——首次启动后改 config
#    文件不会自动生效，要走 worker 控制台或 PUT /api/config。
if [[ ! -f config/agent-worker.yaml ]]; then
    if [[ -n "$BASE" && -f "$BASE/config/agent-worker.yaml" ]]; then
        mkdir -p config
        cp "$BASE/config/agent-worker.yaml" config/agent-worker.yaml
        # host_url 指向本实例的开发后端端口（与 make dev-backend 的 DEV_BACKEND_PORT 一致）。
        # 注册 token 不再经配置文件注入（issue #35）：启动 worker 控制台后在
        # 「Workspace 访问（Scoped Token）」区块粘贴 Host 管理员签发的 scoped token。
        replace_in_place "s|^host_url:.*|host_url: http://127.0.0.1:${DEV_BACKEND_PORT:-8001}|" config/agent-worker.yaml
        replace_in_place "s|^worker_id:.*|worker_id: ${NAME}|" config/agent-worker.yaml
        replace_in_place "s|^name:.*|name: ${NAME} (worktree)|" config/agent-worker.yaml
        echo "已生成 config/agent-worker.yaml <- ${BASE}（host_url/worker_id/name 已改写）"
    else
        echo "提示: 基准 worktree 无 config/agent-worker.yaml，跳过 worker 配置种子" >&2
    fi
fi

cat <<EOF
完成。剩余手工步骤（如未做过）：
  - frontend: cd frontend && npm ci
  - 质量门内环索引: GATE_TIER=aff-index ./scripts/check-quick-backend.sh
    （一次性 ~2.5 分钟，建 .pytest-aff-index.json；此后改动后用
    GATE_TIER=aff ./scripts/check-quick.sh 快速内环，依赖/conftest 变更后重建）
  - 质量门: ./scripts/check-quick.sh
  - 材料存储: 若上方提示跳过了建 bucket，先启动共享 RustFS
    （deploy/compose.host.yaml 的 rustfs 服务）再重跑本脚本；未配置 S3 时
    材料 API 降级为 503，其余功能不受影响
  - workspace 调度: 后端每次启动把全部 workspace 重置为暂停（刻意设计），
    首次启动建表后按需执行 ./scripts/resume-workspaces.sh（或控制台手动恢复）
  - worker: claim 默认关闭（刻意设计），启动后经 worker 控制台（默认 8789）
    或 PUT /api/config 打开 claim_enabled；capabilities/models 已在
    config/agent-worker.yaml 种子配置中声明，首次导入后修改要走控制台/API
EOF
