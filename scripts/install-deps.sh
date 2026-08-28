#!/usr/bin/env bash
# 全新 clone 的一键前置依赖安装与项目初始化（幂等，可重复执行），由
# make install 调用。面向「主仓库根的直接 checkout」（新用户上手路径）；
# git worktree 场景的初始化走 scripts/init-worktree.sh（从基准 worktree
# 复制 .env、按 worktree 名派生专属库/bucket），两者分工不同：本脚本不
# 依赖任何既有 worktree，也不会派生隔离库。
#   1. 检测前置工具：uv、Python 3.11+、Node 18+、PostgreSQL（psql/createdb）、
#      cargo、docker——macOS 缺失项用 brew 补装（先检测后装），其他平台
#      打印安装指引后 fail-fast
#   2. uv sync（Python 依赖）
#   3. createdb agent_legion（已存在跳过；PG 未运行时先尝试 brew services 拉起）
#   4. .env 缺失时从 .env.example 复制，并生成随机 S3 凭据写入（本地 RustFS
#      用；已有 .env 不动）
#   5. deploy/secrets/vault_master_key 缺失时生成（同 init-worktree.sh）
#   6. scripts/ensure-velites.sh --dest data/bin（指纹一致自动跳过）
#   7. frontend/node_modules 缺失时 npm ci
#   8. config/agent-worker.yaml 缺失时从 example 种子（host_url/work_root
#      改写为本机 dev 值）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# BSD/GNU sed 对 `-i` 的参数语法不同；带显式 backup suffix 的形式两边
# 都支持。替换成功后删除备份，避免开发配置目录残留 `.bak`。
replace_in_place() {
    local expression="$1"
    local path="$2"
    sed -i.bak -E "$expression" "$path"
    rm -f "${path}.bak"
}

have() { command -v "$1" >/dev/null 2>&1; }

# 1. 前置工具检测（macOS 缺失项 brew 补装；其他平台打印指引 fail-fast）
IS_MACOS=false
[[ "$(uname)" == "Darwin" ]] && IS_MACOS=true

brew_install() {
    # brew install 失败不静默：打错误并终止（装了一半的工具链比没装更难排查）。
    local name="$1"
    shift
    echo "安装 ${name} …（brew install $*）"
    if ! brew install "$@"; then
        echo "错误: brew install $* 失败，请手工安装 ${name} 后重跑本脚本" >&2
        exit 1
    fi
}

python_ok() {
    have python3 && python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

node_ok() {
    have node && node -e 'process.exit(parseInt(process.version.slice(1)) >= 18 ? 0 : 1)' 2>/dev/null
}

if ! $IS_MACOS; then
    MISSING=()
    have uv || MISSING+=("uv: https://docs.astral.sh/uv/getting-started/installation/")
    python_ok || MISSING+=("Python 3.11+: https://www.python.org/downloads/")
    node_ok || MISSING+=("Node 18+: https://nodejs.org/")
    { have psql && have createdb; } || MISSING+=("PostgreSQL 17: https://www.postgresql.org/download/")
    have cargo || MISSING+=("Rust 工具链: https://rustup.rs/")
    have docker || MISSING+=("Docker: https://docs.docker.com/get-docker/")
    if [[ ${#MISSING[@]} -gt 0 ]]; then
        echo "错误: 缺少前置依赖（非 macOS 平台请按下述指引手工安装后重跑本脚本）:" >&2
        for item in "${MISSING[@]}"; do
            echo "  - $item" >&2
        done
        exit 1
    fi
else
    if ! have brew; then
        echo "错误: 未检测到 Homebrew，请先安装（https://brew.sh）后重跑本脚本:" >&2
        echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"' >&2
        exit 1
    fi
    have uv || brew_install "uv" "uv"
    python_ok || brew_install "Python 3.11+" "python@3.12"
    node_ok || brew_install "Node 18+" "node"
    if ! { have psql && have createdb; }; then
        brew_install "PostgreSQL 17" "postgresql@17"
        # postgresql@17 是 keg-only：本进程内直接挂 bin 目录，并提示写入 shell 配置。
        PG_BIN="$(brew --prefix postgresql@17)/bin"
        export PATH="$PG_BIN:$PATH"
        echo "提示: postgresql@17 为 keg-only，已临时加入 PATH；建议写入 shell 配置: export PATH=\"$PG_BIN:\$PATH\""
    fi
    have cargo || brew_install "Rust 工具链（cargo）" "rust"
    if ! have docker; then
        brew_install "Docker Desktop" "--cask docker"
        echo "提示: Docker Desktop 需手动启动一次完成授权；未启动时 make dev-up 会跳过本地 RustFS（材料 API 降级 503）"
    fi
fi

# 2. Python 依赖
echo "同步 Python 依赖（uv sync）…"
UV_CACHE_DIR=.uv-cache uv sync

# 3. 开发数据库（已存在跳过；PG 未运行时先尝试拉起 brew 服务）
if have createdb; then
    if ! pg_isready -q 2>/dev/null; then
        if $IS_MACOS && have brew && brew list --formula postgresql@17 >/dev/null 2>&1; then
            echo "PostgreSQL 未在运行，尝试 brew services start postgresql@17 …"
            brew services start postgresql@17 >/dev/null 2>&1 || true
        fi
    fi
    if createdb agent_legion 2>/dev/null; then
        echo "已创建数据库 agent_legion"
    else
        echo "数据库 agent_legion 已存在或建库失败（如已存在可忽略；PG 未运行请先启动后重跑本脚本）"
    fi
else
    echo "提示: createdb 不可用（可能刚装完 PostgreSQL），新开 shell 后重跑本脚本即可补齐建库" >&2
fi

# 4. .env（缺失时从 example 复制并生成随机 S3 凭据；已有 .env 不动）
if [[ ! -f .env ]]; then
    cp .env.example .env
    ACCESS_KEY="$(openssl rand -hex 20)"
    SECRET_KEY="$(openssl rand -hex 40)"
    replace_in_place "s|^AGENT_LEGION_S3_ACCESS_KEY=.*|AGENT_LEGION_S3_ACCESS_KEY=${ACCESS_KEY}|" .env
    replace_in_place "s|^AGENT_LEGION_S3_SECRET_KEY=.*|AGENT_LEGION_S3_SECRET_KEY=${SECRET_KEY}|" .env
    echo "已生成 .env <- .env.example（AGENT_LEGION_S3_ACCESS_KEY/SECRET_KEY 已填随机值，本地 RustFS 用）"
else
    echo ".env 已存在，跳过"
fi

# 5. deploy/secrets/vault_master_key（env-only 配置，缺失时 vault 写入会抛错）
mkdir -p deploy/secrets
if [[ ! -s deploy/secrets/vault_master_key ]]; then
    UV_CACHE_DIR=.uv-cache uv run python -c \
        "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
        > deploy/secrets/vault_master_key
    echo "已生成 deploy/secrets/vault_master_key"
fi
chmod 600 deploy/secrets/vault_master_key

# 6. velites 二进制（指纹一致自动跳过）
./scripts/ensure-velites.sh --dest data/bin

# 7. 前端依赖
if [[ ! -d frontend/node_modules ]]; then
    echo "安装前端依赖（npm ci）…"
    (cd frontend && npm ci)
else
    echo "frontend/node_modules 已存在，跳过 npm ci"
fi

# 8. Worker 引导配置（缺失时从 example 种子，改写本机 dev 字段）
if [[ ! -f config/agent-worker.yaml ]]; then
    cp config/agent-worker.example.yaml config/agent-worker.yaml
    replace_in_place "s|^host_url:.*|host_url: http://127.0.0.1:${DEV_BACKEND_PORT:-8001}|" config/agent-worker.yaml
    replace_in_place "s|^work_root:.*|work_root: data/agent-worker|" config/agent-worker.yaml
    echo "已生成 config/agent-worker.yaml <- example（host_url/work_root 已改写）"
else
    echo "config/agent-worker.yaml 已存在，跳过"
fi

cat <<EOF
完成。下一步：
  - 启动开发环境: make dev-up（自动起本地 RustFS 并建 bucket；未装/未启动
    docker 时材料 API 降级 503，其余功能不受影响）
  - 查看状态: make dev-status；停止: make dev-down
  - （可选）安装本地质量门钩子: make install-hooks
  - 材料存储切云端 S3: 改 .env 的 AGENT_LEGION_S3_ENDPOINT/凭据/bucket 三样
    即可，本地 RustFS 会被自动跳过（详见 docs/materials-storage-deployment.md）
EOF
