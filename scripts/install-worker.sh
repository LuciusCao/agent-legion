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
# 对应 tag（即经过 worker-image-release workflow 发布过）。
WORKER_VERSION="${AGENT_WORKER_VERSION:-0.6.1}"
VELITES_VERSION="${VELITES_VERSION:-0.5.0}"
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
  --version TAG         worker 镜像 tag（默认 0.6.1；须存在 worker-v<TAG>
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

# YAML 单引号风格转义（codex P2）：用户可控字符串（--name / --worker-id /
# --host-url）写入 worker.yaml 前统一处理——值内单引号加倍，整体再包单引号；
# 否则 "Worker: west" 产生非法 YAML、"Worker #1" 被 # 截断成注释。
yaml_sq() {
  printf "'%s'" "$(printf '%s' "$1" | sed "s/'/''/g")"
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

# COMPOSE_REF 必须在参数解析后计算（codex P1）：--version 改变镜像版本时
# compose ref 要同步跟随，否则升级会静默组合「新镜像 × 旧编排文件」。
# AGENT_WORKER_COMPOSE_REF 可整体覆盖拉取 ref（如 develop），测试/逃生口。
COMPOSE_REF="${AGENT_WORKER_COMPOSE_REF:-worker-v${WORKER_VERSION}}"

command -v docker >/dev/null 2>&1 || die "docker 未安装"
docker compose version >/dev/null 2>&1 || die "docker compose 子命令不可用（需 Compose v2.24+）"
command -v curl >/dev/null 2>&1 || die "curl 未安装"
# env_file long syntax（required: false）需要 Compose v2.24+；版本串格式
# 各发行版不一，匹配不上只警告不阻断（认不出的格式放行，报解析错时先
# 升级 compose——subagent P3）。
if ! docker compose version --short 2>/dev/null | grep -qE '^v?(2\.(2[4-9]|[3-9][0-9])|[3-9])\.'; then
  echo "警告: docker compose 版本可能低于 v2.24（env_file long syntax 需要），up 报解析错时先升级 compose" >&2
fi

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

# 输入校验（codex P2：发生在任何写入之前——路径写错应在安装目录被触碰
# 前就失败，而不是留下半应用状态）
[ -z "$MODELS_JSON" ] || [ -f "$MODELS_JSON" ] || die "--models-json 指向的文件不存在: $MODELS_JSON"

mkdir -p "$TARGET/velites-bin" "$TARGET/velites-config" "$TARGET/pi-config"
cd "$TARGET"

echo "==> 安装目录: $TARGET"
echo "==> 镜像: ${IMAGE_REPO}:${WORKER_VERSION}（linux/${WORKER_ARCH}）"
echo "==> velites: ${VELITES_VERSION}（${VELITES_TRIPLE}）"

# ---- 1. 下载与校验阶段（codex P2：远端资产全部先备妥在临时目录）----
# 任何一步失败都不触碰安装目录，杜绝「声明为目标版本、实际半应用」的
# 状态；全部通过后才进入提交阶段统一落位。
TMPDIR_="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_"' EXIT
# POSIX sh 不保证 SIGINT/SIGTERM 时触发 EXIT trap（subagent P3）：显式转
# exit 让 EXIT trap 接管清理，Ctrl-C 中断下载不在 /tmp 留残骸。
trap 'exit 1' INT TERM HUP

# ---- 1a. compose 文件（自有资产：拉取失败时保留现有，不算硬错误）----
COMPOSE_URL="${REPO_RAW_BASE}/${COMPOSE_REF}/deploy/compose.worker.standalone.yaml"
compose_new=0
if curl -fsSL -o "$TMPDIR_/docker-compose.yaml" "$COMPOSE_URL"; then
  # 语法/插值预检（subagent P3）：透明代理返回 200 + HTML 时会原样落位，
  # 失败推迟到 up 才爆且难排查；config 校验把问题提前到下载时刻。
  if ! docker compose -f "$TMPDIR_/docker-compose.yaml" config --quiet 2>"$TMPDIR_/compose-config.err"; then
    die "拉取的 compose 文件校验失败（内容异常？透明代理/中间人返回了非 YAML）: $(tail -1 "$TMPDIR_/compose-config.err")"
  fi
  compose_new=1
else
  if [ -f ./docker-compose.yaml ]; then
    echo "警告: 拉取 ${COMPOSE_URL} 失败，保留现有 docker-compose.yaml" >&2
  else
    die "拉取 ${COMPOSE_URL} 失败且本地无现有文件（检查 ref ${COMPOSE_REF} 是否存在）"
  fi
fi

# ---- 1b. velites 二进制（版本戳命中即跳过；sha256 校验在临时目录完成）----
velites_new=0
VELITES_TARBALL="velites-${VELITES_VERSION}-${VELITES_TRIPLE}.tar.gz"
# -f 而非 -x：-x 对目录同样放行（subagent P2）——bind 源缺失时 daemon
# 自动建的空目录会被误认成已安装的二进制，且后续重跑永远命中跳过分支。
if [ -f ./velites-bin/velites ] && [ "$(cat ./velites-bin/.velites-version 2>/dev/null || true)" = "$VELITES_VERSION" ]; then
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
  velites_new=1
  echo "==> velites ${VELITES_VERSION} 已下载并校验（sha256 通过）"
fi

# ---- 2. 提交阶段（资产已全部备妥，逐项原子落位）----
# ---- 2a. compose 文件 ----
if [ "$compose_new" = 1 ]; then
  # 原子替换（mv 同盘）；compose 自身插值默认值与 .env 的显式声明保持一致
  mv -f "$TMPDIR_/docker-compose.yaml" ./docker-compose.yaml
  echo "==> compose 文件已更新（ref: ${COMPOSE_REF}）"
fi

# 混合态守卫（subagent P2）：compose 拉取失败保留旧文件时，若本次是版本
# 变更运行，继续会把 .env 刷成新版本——形成「新镜像声明 × 旧编排文件」
# 的混合态（正是 codex P1 修复要消灭的组合）。版本变更必须 compose 与
# 镜像同步切换，拉不到就整体失败。
env_image_now="$(sed -n 's/^AGENT_WORKER_IMAGE=//p' ./.env 2>/dev/null | tail -1)"
if [ "$compose_new" = 0 ] && [ -n "$env_image_now" ] && [ "$env_image_now" != "${IMAGE_REPO}:${WORKER_VERSION}" ]; then
  die "compose 拉取失败（ref ${COMPOSE_REF}）且本次为版本变更（${env_image_now} → ${WORKER_VERSION}）：保留旧编排而刷新 .env 会制造镜像×编排混合态，已中止。确认 worker-v${WORKER_VERSION} tag 已发布后重跑"
fi

# ---- 2b. .env：自管行（AGENT_WORKER_IMAGE / AGENT_WORKER_UI_BIND /
# AGENT_WORKER_UI_PORT）由脚本维护，其余内容不动 ----
if [ ! -f ./.env ]; then
  cat > ./.env <<'EOF'
# compose 插值变量（本文件只被 compose 读取，不进容器；chmod 600 保护凭据）
# LLM_GATEWAY_TOKEN=<gateway-token>   # models.json 中 apiKey 引用 $LLM_GATEWAY_TOKEN 时取消注释
EOF
  echo "==> .env 已创建"
fi
# 尾换行兜底：无尾换行的自建 .env 会把下面的追加行粘进末行
[ -n "$(tail -c 1 ./.env)" ] && printf '\n' >> ./.env

set_self_managed_line() {
  # 原位替换或追加一行自管配置。经临时文件 + mv 落位（不用 sed -i.bak：
  # 那会先覆盖再删除用户可能预先存在的 .env.bak 手工备份，subagent P3）。
  sm_key="$1"; sm_value="$2"
  if grep -q "^${sm_key}=" ./.env; then
    sed "s|^${sm_key}=.*|${sm_key}=${sm_value}|" ./.env > ./.env.staged
    mv -f ./.env.staged ./.env
  else
    printf '%s=%s\n' "$sm_key" "$sm_value" >> ./.env
  fi
}

set_self_managed_line AGENT_WORKER_IMAGE "${IMAGE_REPO}:${WORKER_VERSION}"
# 环境变量携带的 UI 绑定/端口持久化（subagent P2）：只活在本次进程的话，
# 用户事后按脚本指引手动 up、或升级重跑忘带 env，端口冲突就会复发——
# 8787 被本机 dev worker 占用恰是最常见的安装期失败。
if [ -n "${AGENT_WORKER_UI_BIND:-}" ]; then
  set_self_managed_line AGENT_WORKER_UI_BIND "${AGENT_WORKER_UI_BIND}"
fi
if [ -n "${AGENT_WORKER_UI_PORT:-}" ]; then
  set_self_managed_line AGENT_WORKER_UI_PORT "${AGENT_WORKER_UI_PORT}"
fi
chmod 600 ./.env

# ---- 2c. velites 二进制原子安置 + 版本戳 ----
if [ "$velites_new" = 1 ]; then
  # mv 目录陷阱守卫（subagent P2）：目标若被 docker bind 自动建成了
  # **目录**，POSIX mv 会把文件移进目录内部（exit 0），版本戳照写、
  # 「已安装」照报——静默损坏。先移除再安置。
  if [ -d ./velites-bin/velites ]; then
    echo "警告: velites-bin/velites 是目录（疑似 bind 源缺失时 daemon 自动创建的空目录），移除后安置真实二进制" >&2
    rm -rf ./velites-bin/velites
  fi
  cp "$TMPDIR_/velites-${VELITES_VERSION}-${VELITES_TRIPLE}/velites" ./velites-bin/velites.tmp
  chmod +x ./velites-bin/velites.tmp
  mv -f ./velites-bin/velites.tmp ./velites-bin/velites
  echo "$VELITES_VERSION" > ./velites-bin/.velites-version
  echo "==> velites ${VELITES_VERSION} 已安装"
fi

# ---- 2d. worker.yaml 引导配置（用户资产：仅缺失时创建）----
if [ ! -f ./worker.yaml ]; then
  cat > ./worker.yaml <<EOF
host_url: $(yaml_sq "${HOST_URL:-http://<HOST-IP>:8000}")
worker_id: $(yaml_sq "$WORKER_ID")
name: $(yaml_sq "$WORKER_NAME")
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

# ---- 2e. models.json（用户资产：--models-json 显式安装；否则仅缺失时给示例）----
if [ -n "$MODELS_JSON" ]; then
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
  if ! docker compose pull 2>"$TMPDIR_/pull.err"; then
    cat >&2 <<EOF
错误: 拉取 ${IMAGE_REPO}:${WORKER_VERSION} 失败（$(tail -1 "$TMPDIR_/pull.err")）。
两种常见原因：
  - manifest unknown：该版本未发布——检查 worker-v${WORKER_VERSION} tag
    是否存在（发布管道：worker-image-release workflow）；
  - authentication required：GHCR 包默认 private——docker login ghcr.io
    （用户名 + 具 read:packages 权限的 PAT），或在 GitHub package 设置中
    把 agent-legion-worker 改为 public。
EOF
    exit 1
  fi
  # || true：up 失败（如端口绑定）不让 set -e 在此杀掉脚本——真实错误在
  # up.log 里，由下面的状态检查统一解读输出。
  docker compose up -d >"$TMPDIR_/up.log" 2>&1 || true
  # 稳定性采样（subagent P2）：crash loop 容器在 running/restarting 间
  # 震荡，单次快照会踩在 running 点上误报成功——连采 3 次 × 5s，要求至少
  # 连续 2 次采样 state=running 且 RestartCount=0。
  # 检查边界（如实声明）：只覆盖容器进程级失败（端口绑定失败停 created、
  # 镜像入口崩溃进 restart loop）。期望 runtime 守卫的 exit 2 发生在
  # executor 子进程（supervisor 置 failed 不自动重启、容器本身恒
  # running），只能经 healthcheck（unhealthy）与控制台日志暴露，不在此处。
  stable=0
  i=0
  state="?" restarts="?"
  while [ "$i" -lt 3 ]; do
    sleep 5
    i=$((i+1))
    state="$(docker compose ps -a --format '{{.State}}' worker)"
    cid="$(docker compose ps -q worker 2>/dev/null || true)"
    restarts=1
    if [ -n "$cid" ]; then
      restarts="$(docker inspect --format '{{.RestartCount}}' "$cid" 2>/dev/null || echo 1)"
    fi
    if [ "$state" = "running" ] && [ "$restarts" = "0" ]; then
      stable=$((stable+1))
    else
      stable=0
    fi
  done
  if [ "$stable" -lt 2 ]; then
    cat >&2 <<EOF
错误: 容器未稳定运行（最后采样 state=${state} restarts=${restarts}）。常见原因：
  - ports are not available / address already in use：宿主机端口被占——
    在 ${TARGET}/.env 加 AGENT_WORKER_UI_PORT=<其它端口> 后重跑本脚本；
  - restarts>0（容器入口崩溃循环）：看下方日志。
EOF
    cat >&2 "$TMPDIR_/up.log"
    docker compose logs --tail 30 worker >&2 || true
    exit 1
  fi
  echo "==> Worker 容器已稳定运行（粘贴 scoped token 前显示 unhealthy 属预期，见下方步骤 2）"
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

# 实际生效的 UI 地址（成功消息不再硬编码 8787——subagent P2：端口插值
# 存在时误导用户去错误地址）。优先级与 compose 插值一致：.env >
# 默认值（脚本自身的环境变量值已在 .env 持久化，读回即可）。
ui_host_final="$(sed -n 's/^AGENT_WORKER_UI_BIND=//p' ./.env 2>/dev/null | tail -1)"
ui_host_final="${ui_host_final:-127.0.0.1}"
ui_port_final="$(sed -n 's/^AGENT_WORKER_UI_PORT=//p' ./.env 2>/dev/null | tail -1)"
ui_port_final="${ui_port_final:-8787}"

cat <<EOF

安装完成。后续步骤：
  1. 打开 Worker 控制台 http://${ui_host_final}:${ui_port_final}（本机浏览器）；
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
