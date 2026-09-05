#!/bin/sh
# Agent Legion Worker 一键安装脚本（curl | sh 形态，无需克隆仓库）：
#
#   curl -fsSL https://raw.githubusercontent.com/LuciusCao/agent-legion/develop/scripts/install-worker.sh \
#     | sh -s -- --host-url http://<host-ip>:8000 --worker-id my-worker-1
#
# 在目标目录（默认 ~/agent-legion-worker）组装独立部署并启动：
#   - docker-compose.yaml：从 worker-v* tag ref 拉取 deploy/compose.worker.standalone.yaml
#   - velites-bin/velites：GitHub Release 的 linux 二进制（sha256 校验，架构自动匹配）
#   - worker.yaml：引导配置（仅首次创建；此后以 Worker 控制台为准）
#   - velites-config/models.json(.example)：provider 注册表（用户资产，仅缺失时生成示例）
#   - .env：compose 插值变量（AGENT_WORKER_IMAGE 由本脚本管理，其余用户资产）
#
# 幂等语义（重复执行收敛到同一状态）：
#   - 自有文件（compose、velites 二进制、.env 的 AGENT_WORKER_IMAGE 行）刷新到目标版本；
#   - 用户资产（worker.yaml、models.json、.env 其余内容、velites-provider.env）
#     已存在即跳过，绝不覆盖——worker.yaml 首次启动导入控制卷后，覆盖只会制造
#     mounted_config_diverged；
#   - velites 按 .velites-version 戳跳过重复下载；compose 拉取失败时保留现有文件。
#
# 约束：POSIX sh（curl 管道可能落在 dash 上，不用 bashism、不读 stdin——
# 管道模式下 stdin 是脚本本体）；Docker Compose v2.24+（env_file long syntax）；
# GHCR 包默认 private，需先 docker login ghcr.io（read:packages 的 PAT）或改 public。
set -eu

REPO_RAW_BASE="https://raw.githubusercontent.com/LuciusCao/agent-legion"
GITHUB_RELEASE_BASE="https://github.com/LuciusCao/agent-legion/releases/download"
IMAGE_REPO="ghcr.io/luciuscao/agent-legion-worker"
# 默认钉在已发布版本；--version / --velites-version 或同名环境变量覆盖。
# 注意 compose 文件按 worker-v<version> tag ref 拉取：自定义版本必须存在
# 对应 tag（即经过 worker-image-release workflow 发布过）。AGENT_WORKER_
# COMPOSE_REF 可整体覆盖拉取 ref（如 develop），仅作测试/逃生口。
WORKER_VERSION="${AGENT_WORKER_VERSION:-0.6.1}"
VELITES_VERSION="${VELITES_VERSION:-0.5.0}"
COMPOSE_REF="${AGENT_WORKER_COMPOSE_REF:-worker-v${WORKER_VERSION}}"
TARGET="${AGENT_WORKER_INSTALL_DIR:-$HOME/agent-legion-worker}"
HOST_URL=""
WORKER_ID=""
WORKER_NAME=""
MODELS_JSON=""
NO_UP=0

usage() {
  cat <<'EOF'
用法: install-worker.sh [--target DIR] [--host-url URL] [--worker-id ID]
                        [--name NAME] [--models-json FILE] [--version TAG]
                        [--velites-version VER] [--no-up]

  --target DIR          安装目录（默认 ~/agent-legion-worker，环境变量
                        AGENT_WORKER_INSTALL_DIR 同效）
  --host-url URL        Host API 地址，如 http://192.0.2.1:8000（写入引导
                        worker.yaml；之后可随时在 Worker 控制台改）
  --worker-id ID        Worker 标识（默认 <hostname>-worker）
  --name NAME           显示名（默认 "Worker on <hostname>"）
  --models-json FILE    安装该文件为 velites-config/models.json（已存在则
                        覆盖——显式传入即声明为本次的期望内容）
  --version TAG         worker 镜像 tag（默认 0.6.0；须存在 worker-v<TAG>
                        发布 tag）
  --velites-version VER velites 二进制版本（默认 0.5.0）
  --no-up               只组装文件，不执行 docker compose up

示例（无仓库的远程机器）:
  curl -fsSL .../install-worker.sh | sh -s -- \
    --host-url http://192.0.2.1:8000 --worker-id macbook-1 \
    --models-json ./models.json
EOF
}

die() {
  echo "错误: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:?--target 缺参数}"; shift 2 ;;
    --host-url) HOST_URL="${2:?--host-url 缺参数}"; shift 2 ;;
    --worker-id) WORKER_ID="${2:?--worker-id 缺参数}"; shift 2 ;;
    --name) WORKER_NAME="${2:?--name 缺参数}"; shift 2 ;;
    --models-json) MODELS_JSON="${2:?--models-json 缺参数}"; shift 2 ;;
    --version) WORKER_VERSION="${2:?--version 缺参数}"; shift 2 ;;
    --velites-version) VELITES_VERSION="${2:?--velites-version 缺参数}"; shift 2 ;;
    --no-up) NO_UP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1（--help 查看用法）" ;;
  esac
done

# 版本串进入 URL / .env / sed 表达式，钉死字符集（同时挡住路径注入）。
case "$WORKER_VERSION" in
  ''|*[!a-zA-Z0-9._-]*) die "非法 --version: ${WORKER_VERSION}（允许 [a-zA-Z0-9._-]）" ;;
esac
case "$VELITES_VERSION" in
  ''|*[!a-zA-Z0-9._-]*) die "非法 --velites-version: ${VELITES_VERSION}" ;;
esac

command -v docker >/dev/null 2>&1 || die "docker 未安装"
docker compose version >/dev/null 2>&1 || die "docker compose 子命令不可用（需 Compose v2.24+）"
command -v curl >/dev/null 2>&1 || die "curl 未安装"

# 容器架构跟随宿主 Docker 的原生架构（Docker Desktop on Apple Silicon 跑
# linux/arm64 容器）；不要在 arm64 宿主上装 x86_64 二进制（exec format error
# 只会在运行时爆，期望 runtime 守卫会把它转成启动失败）。
case "$(uname -m)" in
  arm64|aarch64)
    VELITES_TRIPLE="aarch64-unknown-linux-gnu"
    WORKER_ARCH="arm64"
    ;;
  x86_64|amd64)
    VELITES_TRIPLE="x86_64-unknown-linux-gnu"
    WORKER_ARCH="amd64"
    ;;
  *) die "不支持的宿主架构: $(uname -m)（容器为 linux/amd64|arm64）" ;;
esac

HOSTNAME_="$(hostname)"
[ -n "$WORKER_ID" ] || WORKER_ID="${HOSTNAME_}-worker"
[ -n "$WORKER_NAME" ] || WORKER_NAME="Worker on ${HOSTNAME_}"

# 相对路径在 cd 前解析（--models-json 相对调用者 cwd 传参是常态）
ORIG_PWD="$(pwd)"
case "$MODELS_JSON" in
  /*) ;;
  *) [ -z "$MODELS_JSON" ] || MODELS_JSON="${ORIG_PWD}/${MODELS_JSON}" ;;
esac

mkdir -p "$TARGET/velites-bin" "$TARGET/velites-config" "$TARGET/pi-config"
cd "$TARGET"

echo "==> 安装目录: $TARGET"
echo "==> 镜像: ${IMAGE_REPO}:${WORKER_VERSION}（linux/${WORKER_ARCH}）"
echo "==> velites: ${VELITES_VERSION}（${VELITES_TRIPLE}）"

# ---- 1. compose 文件（自有资产：刷新到目标版本；拉取失败保留现有）----
COMPOSE_URL="${REPO_RAW_BASE}/${COMPOSE_REF}/deploy/compose.worker.standalone.yaml"
TMPDIR_="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_"' EXIT
if curl -fsSL -o "$TMPDIR_/docker-compose.yaml" "$COMPOSE_URL"; then
  # 原子替换（mv 同盘）；清理 compose 自身插值默认值与 .env 的显式声明保持一致
  mv -f "$TMPDIR_/docker-compose.yaml" ./docker-compose.yaml
  echo "==> compose 文件已更新（ref: ${COMPOSE_REF}）"
else
  if [ -f ./docker-compose.yaml ]; then
    echo "警告: 拉取 ${COMPOSE_URL} 失败，保留现有 docker-compose.yaml" >&2
  else
    die "拉取 ${COMPOSE_URL} 失败且本地无现有文件（检查 ref ${COMPOSE_REF} 是否存在）"
  fi
fi

# ---- 2. .env：AGENT_WORKER_IMAGE 由脚本管理，其余内容不动 ----
if [ ! -f ./.env ]; then
  cat > ./.env <<'EOF'
# compose 插值变量（本文件只被 compose 读取，不进容器；chmod 600 保护凭据）
# LLM_GATEWAY_TOKEN=<gateway-token>   # models.json 中 apiKey 引用 $LLM_GATEWAY_TOKEN 时取消注释
EOF
  echo "==> .env 已创建"
fi
if grep -q '^AGENT_WORKER_IMAGE=' ./.env 2>/dev/null; then
  sed -i.bak "s|^AGENT_WORKER_IMAGE=.*|AGENT_WORKER_IMAGE=${IMAGE_REPO}:${WORKER_VERSION}|" ./.env
  rm -f ./.env.bak
else
  printf '\nAGENT_WORKER_IMAGE=%s:%s\n' "$IMAGE_REPO" "$WORKER_VERSION" >> ./.env
fi
chmod 600 ./.env

# ---- 3. velites 二进制（版本戳命中即跳过；sha256 校验后原子安置）----
VELITES_TARBALL="velites-${VELITES_VERSION}-${VELITES_TRIPLE}.tar.gz"
if [ -x ./velites-bin/velites ] && [ "$(cat ./velites-bin/.velites-version 2>/dev/null || true)" = "$VELITES_VERSION" ]; then
  echo "==> velites ${VELITES_VERSION} 已就位，跳过下载"
else
  curl -fsSL -o "$TMPDIR_/velites.tar.gz" \
    "${GITHUB_RELEASE_BASE}/velites-v${VELITES_VERSION}/${VELITES_TARBALL}"
  curl -fsSL -o "$TMPDIR_/sha256.txt" \
    "${GITHUB_RELEASE_BASE}/velites-v${VELITES_VERSION}/sha256.txt"
  expected="$(awk -v f="$VELITES_TARBALL" '$2 == f {print $1}' "$TMPDIR_/sha256.txt")"
  [ -n "$expected" ] || die "sha256.txt 中没有 ${VELITES_TARBALL} 的条目（版本与 Release 不匹配？）"
  # sha256sum（Linux）与 shasum -a 256（macOS）双兼容
  actual="$(sha256sum "$TMPDIR_/velites.tar.gz" 2>/dev/null || shasum -a 256 "$TMPDIR_/velites.tar.gz" | awk '{print $1}')"
  actual="${actual%% *}"
  [ "$actual" = "$expected" ] || die "velites tarball 校验失败（期望 ${expected}，实际 ${actual}）"
  tar -xzf "$TMPDIR_/velites.tar.gz" -C "$TMPDIR_"
  cp "$TMPDIR_/velites-${VELITES_VERSION}-${VELITES_TRIPLE}/velites" ./velites-bin/velites.tmp
  chmod +x ./velites-bin/velites.tmp
  mv -f ./velites-bin/velites.tmp ./velites-bin/velites
  echo "$VELITES_VERSION" > ./velites-bin/.velites-version
  echo "==> velites ${VELITES_VERSION} 已安装（sha256 校验通过）"
fi

# ---- 4. worker.yaml 引导配置（用户资产：仅缺失时创建）----
if [ ! -f ./worker.yaml ]; then
  cat > ./worker.yaml <<EOF
host_url: ${HOST_URL:-http://<HOST-IP>:8000}
worker_id: ${WORKER_ID}
name: ${WORKER_NAME}
disabled_runtimes: []
max_concurrency: 10
labels: {os: linux, arch: ${WORKER_ARCH}, location: remote}
work_root: /var/lib/agent-legion-worker
EOF
  chmod 600 ./worker.yaml
  echo "==> worker.yaml 已创建（worker_id=${WORKER_ID}）"
  [ -n "$HOST_URL" ] || echo "警告: 未传 --host-url，worker.yaml 中是占位地址，请稍后在控制台修改" >&2
else
  echo "==> worker.yaml 已存在，跳过（改配置请走 Worker 控制台）"
fi

# ---- 5. models.json（用户资产：--models-json 显式安装；否则仅缺失时给示例）----
if [ -n "$MODELS_JSON" ]; then
  [ -f "$MODELS_JSON" ] || die "--models-json 指向的文件不存在: $MODELS_JSON"
  cp "$MODELS_JSON" ./velites-config/models.json
  chmod 600 ./velites-config/models.json
  echo "==> models.json 已从 ${MODELS_JSON} 安装"
elif [ ! -f ./velites-config/models.json ]; then
  cat > ./velites-config/models.json.example <<'EOF'
{
  "providers": {
    "gateway": {
      "api": "openai-completions",
      "baseUrl": "http://<HOST-IP>:8788/v1",
      "apiKey": "$LLM_GATEWAY_TOKEN",
      "models": ["<model-id>"]
    }
  }
}
EOF
  echo "==> models.json 缺失，已生成 velites-config/models.json.example（参考后创建 models.json）"
fi

# ---- 6. 启动（models.json 未就绪或 --no-up 时跳过，输出指引）----
start_worker() {
  if ! docker compose pull; then
    cat >&2 <<EOF
错误: 拉取 ${IMAGE_REPO} 失败。GHCR 包默认 private：
  - docker login ghcr.io（用户名 + 具 read:packages 权限的 PAT），或
  - 在 GitHub package 设置中把 agent-legion-worker 改为 public。
EOF
    exit 1
  fi
  # --wait：等待 healthcheck 变 healthy（Host 不可达不阻塞——worker 进程
  # 会带退避重试注册，属健康状态）。
  docker compose up -d --wait --wait-timeout 180
  echo "==> Worker 已启动"
}

if [ "$NO_UP" = "1" ]; then
  echo "==> --no-up：跳过启动（之后在 ${TARGET} 执行 docker compose up -d）"
elif [ -f ./velites-config/models.json ]; then
  start_worker
else
  cat <<EOF
==> models.json 未就绪，跳过启动（期望 runtime 守卫会在模型发现失败时
    fail-fast，空启动只会 crash loop）。就绪后二选一：
    - 重跑本安装脚本（幂等），或
    - cd ${TARGET} && docker compose up -d
EOF
fi

cat <<EOF

安装完成。后续步骤：
  1. 打开 Worker 控制台 http://127.0.0.1:8787（本机浏览器）；
  2. 在「Workspace 访问」粘贴 Host 签发的 scoped token（Host Web UI 的
     workspace 设置 → Agent 与 Worker）；
  3. 点「开始领取」（claim_enabled 每次进程启动都重置为关闭，刻意设计）；
  4. 容器内冒烟（三条都过再承接生产任务，命令见
     docs/agent-worker-deployment.md §7）：
       docker compose exec worker python3 -c "import urllib.request; urllib.request.urlopen('http://<HOST-IP>:8000/api/health', timeout=5)"
  5. macOS 提醒：系统睡眠 = Worker 掉线（设置里关睡眠或 caffeinate -dims）。

升级：重跑本脚本并带 --version <新版本>（compose 文件、镜像、.env 同步刷新；
worker.yaml / models.json 等用户资产不动）。
EOF
