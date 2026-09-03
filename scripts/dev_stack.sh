#!/usr/bin/env bash
# 开发环境一键启停：backend (uvicorn --reload) + frontend (vite) + worker service，
# 由 make dev-up / dev-down / dev-status 调用；启动命令复用 Makefile 的
# dev-backend / dev-frontend / dev-worker target（端口变量经环境透传）。
# up 开头会按 scripts/local-s3-decide.sh 的决策带起本地对象存储（默认
# seaweedfs，BACKEND=rustfs 切逃生舱；docker compose 托管）并确保 bucket
# 存在——切外部 S3（.env 配远程 endpoint）后自动跳过。
# 幂等：端口已被监听视为该组件在运行，up 跳过启动、down 只停监听中的组件。
# 进程经 nohup 脱离终端，日志在 data/logs/dev-{backend,frontend,worker}.log。
# 多 worktree 隔离靠 DEV_BACKEND_PORT / DEV_FRONTEND_PORT / AGENT_WORKER_UI_PORT。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${DEV_BACKEND_PORT:-8001}"
FRONTEND_PORT="${DEV_FRONTEND_PORT:-5174}"
WORKER_PORT="${AGENT_WORKER_UI_PORT:-8789}"
LOG_DIR="data/logs"

port_listening() {
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

http_ok() {
    curl -sS -m 2 -o /dev/null "http://127.0.0.1:$1$2" 2>/dev/null
}

print_summary() {
    cat <<EOF
开发环境已就绪：
  后端 API      http://127.0.0.1:${BACKEND_PORT}
  前端控制台    http://127.0.0.1:${FRONTEND_PORT}
  Worker 控制台 http://127.0.0.1:${WORKER_PORT}
  日志：${LOG_DIR}/dev-{backend,frontend,worker}.log
  停止：make dev-down；状态：make dev-status
EOF
}

start_component() {
    local name="$1" port="$2" target="$3" log="$4"
    if port_listening "$port"; then
        echo "${name}已在 :$port 运行，跳过"
    else
        echo "启动${name} :$port …（日志 ${log}）"
        nohup make "$target" > "$log" 2>&1 &
    fi
}

# 从根 .env 读一个键的值（进程环境优先；去 export 前缀、首尾空白与一层
# 配对引号，与 dotenv 语义对齐）。compose 插值只读 deploy/.env，dev 形态
# 只有根 .env 一份，起本地对象存储容器前靠它显式 export 凭据，避免容器
# root 凭据与后端读取的 .env 不一致。
# 注意：这是仓库里第二份 shell dotenv 解析（另一份在
# scripts/local-s3-decide.sh 的 lookup/_dotenv_value，语义更完整）；本函数
# 语义刻意更窄——只取第一个匹配键、空值返回 1，仅够读扁平 KEY=VALUE
# （S3 十六进制凭据/绑定地址）。需要更完整语义时不要各自扩展，应合并实现。
read_env_value() {
    local key="$1" line value
    value="$(printenv "$key" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
        printf '%s' "$value"
        return 0
    fi
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" .env 2>/dev/null | head -n 1 || true)"
    [[ -n "$line" ]] || return 1
    value="${line#*=}"
    value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    if [[ ${#value} -ge 2 && "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
    elif [[ ${#value} -ge 2 && "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
}

# 本地对象存储（材料存储后端）：决策逻辑与 prod 入口共用
# scripts/local-s3-decide.sh（AGENT_LEGION_LOCAL_S3=auto|always|never，
# 默认 auto：endpoint 指向本机或未配置 S3 → start；远程 → skip 并打原因）。
# 决策/启动/bucket 任何一步失败都只告警不阻断——材料 API 降级为 503，
# 其余功能不受影响（与 native-prod-up.sh 同一策略）。
# 本地后端分派（#340）：BACKEND=seaweedfs（默认）→ seaweedfs 服务；rustfs
# → rustfs 服务。服务名统一经 local-s3-decide.sh --service-name 解析（分派
# 表只在 decide 一处维护，本脚本不重写第三份）；就绪端口按服务分流。
ensure_local_object_store() {
    local decision rc=0 service
    decision="$(scripts/local-s3-decide.sh .env)" || rc=$?
    # 服务名经 decide 统一分派；输出非法（脚本异常/旧版无 --service-name）
    # 时回落默认后端而不是把垃圾值传给 up -d。
    service="$(scripts/local-s3-decide.sh --service-name .env 2>/dev/null || true)"
    case "$service" in
        seaweedfs | rustfs) ;;
        *) service=seaweedfs ;;
    esac
    if [[ "$rc" -eq 2 ]]; then
        # 开关值非法：prod 入口 fail-fast，dev 降级为告警（不阻断开发启动）。
        echo "警告: AGENT_LEGION_LOCAL_S3/BACKEND 开关值非法（原因见上方），跳过本地对象存储" >&2
        return 0
    fi
    if [[ "$decision" != "start" ]]; then
        if [[ "$rc" -ne 0 ]]; then
            # 决策为 start 但凭据未配齐（rc 3）：补齐 .env 的
            # AGENT_LEGION_S3_ACCESS_KEY/SECRET_KEY 后重跑即可。
            echo "警告: 跳过本地 ${service} 启动（原因见上方），材料相关功能将不可用" >&2
        fi
        return 0
    fi
    if ! command -v docker >/dev/null 2>&1; then
        echo "提示: 未检测到 docker，跳过本地 ${service}；材料 API 将降级为 503（其余功能不受影响）" >&2
        return 0
    fi
    local compose_files=(-f deploy/compose.host.yaml)
    [[ -f deploy/compose.local.yaml ]] && compose_files+=(-f deploy/compose.local.yaml)
    # 防 recreate（PR #232）：compose.host.yaml 固定 `name: agent-legion`，
    # 全机（prod + 所有 worktree）共用同一个本地对象存储容器；凭据与本机
    # 不同时 up -d 会因 config hash 变化 recreate 容器，持旧凭据的 prod
    # 立刻材料 503。已在运行就跳过 up -d 直接确认 bucket——容器在但凭据
    # 不匹配会在建 bucket 处自然告警暴露。
    if docker compose "${compose_files[@]}" ps --status running --services 2>/dev/null \
        | grep -qx "$service"; then
        echo "${service} 容器已在运行（可能与 prod/其他 worktree 共享），跳过 recreate，直接确认 bucket"
    else
        local access_key secret_key
        access_key="$(read_env_value AGENT_LEGION_S3_ACCESS_KEY || true)"
        secret_key="$(read_env_value AGENT_LEGION_S3_SECRET_KEY || true)"
        if [[ -z "$access_key" || -z "$secret_key" ]]; then
            # 走到这里说明是「完全未配置 S3 → start」的零配置路径（已表达本地
            # 存储意图的缺凭据场景已被 local-s3-decide.sh rc 3 拦在上方）。
            echo "提示: 未配 AGENT_LEGION_S3_ACCESS_KEY/SECRET_KEY，${service} 使用镜像默认凭据（仅 loopback 绑定）" >&2
        fi
        echo "启动 ${service} 容器（首次运行需拉取镜像，可能耗时数分钟）…"
        # 显式指定服务名时所在 profile 自动启用；幂等，已运行则 no-op。
        if ! AGENT_LEGION_S3_ACCESS_KEY="$access_key" \
            AGENT_LEGION_S3_SECRET_KEY="$secret_key" \
            docker compose "${compose_files[@]}" up -d "$service"; then
            echo "警告: ${service} 启动失败，材料相关功能将不可用；" >&2
            echo "      可手工重跑: docker compose ${compose_files[*]} up -d ${service}" >&2
            echo "      （详见 docs/materials-storage-deployment.md）" >&2
            return 0
        fi
        echo "${service}（材料对象存储）已就绪"
    fi
    # 等 S3 API 就绪后确保 bucket+CORS（ensure-s3-bucket.py 与
    # init-worktree.sh 共用）；超时/失败仅告警，就绪后重跑 make dev-up 补齐。
    # 注：就绪探测用 /healthz——seaweedfs 与 rustfs 都对根路径 / 返回 403
    # （S3 API 匿名行为，#340 教训），http_ok 判 2xx，403 不算就绪。
    for _ in $(seq 1 15); do
        if [[ "$service" == "seaweedfs" ]]; then
            http_ok 8333 /healthz && break
        else
            http_ok 9000 /minio/health/live && break
        fi
        sleep 2
    done
    if PYTHONPATH="$ROOT" UV_CACHE_DIR=.uv-cache uv run python scripts/ensure-s3-bucket.py .env; then
        :
    else
        echo "警告: 建 bucket 失败（endpoint 可能尚未就绪），材料 API 暂降级 503；" >&2
        echo "      ${service} 就绪后重跑 make dev-up 即可补齐。" >&2
    fi
}

cmd_up() {
    mkdir -p "$LOG_DIR"
    ensure_local_object_store
    if [[ ! -d frontend/node_modules ]]; then
        echo "安装前端依赖…"
        (cd frontend && npm ci)
    fi

    start_component "后端" "$BACKEND_PORT" dev-backend "$LOG_DIR/dev-backend.log"
    start_component "前端" "$FRONTEND_PORT" dev-frontend "$LOG_DIR/dev-frontend.log"
    # worker 唯一生效配置是状态副本（issue #323）；无状态副本的 worktree 跳过 Worker
    if [[ -f config/agent-worker.yaml ]]; then
        echo "提示: config/agent-worker.yaml 已退役（#323），不再被读取，可删除；生效配置是 data/agent-worker-service/worker.yaml" >&2
    fi
    if [[ -f data/agent-worker-service/worker.yaml ]]; then
        start_component "Worker" "$WORKER_PORT" dev-worker "$LOG_DIR/dev-worker.log"
    else
        echo "缺少 data/agent-worker-service/worker.yaml（先跑 scripts/init-worktree.sh 种子），跳过 Worker" >&2
    fi

    for _ in $(seq 1 45); do
        local ok=true
        http_ok "$BACKEND_PORT" /api/health || ok=false
        http_ok "$FRONTEND_PORT" / || ok=false
        if [[ -f data/agent-worker-service/worker.yaml ]]; then
            http_ok "$WORKER_PORT" /api/health || ok=false
        fi
        if $ok; then
            print_summary
            return 0
        fi
        sleep 2
    done
    echo "有服务未在预期时间内就绪，日志见 $LOG_DIR/dev-*.log" >&2
    return 1
}

stop_port() {
    local port="$1" name="$2" grace="$3"
    local pid
    pid="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1 || true)"
    if [[ -z "$pid" ]]; then
        echo "$name :$port 未在运行，跳过"
        return 0
    fi
    # 监听端口的是子进程（uvicorn --reload 的 server、npm 拉起的 vite）：
    # 只杀子进程父进程会把它重新拉起，父（reloader / npm）要一起 SIGTERM。
    local ppid
    ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    echo "停止 $name :$port (pid $pid) …"
    if [[ -n "$ppid" && "$ppid" != "1" ]]; then
        kill "$ppid" 2>/dev/null || true
    fi
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 "$grace"); do
        if ! port_listening "$port"; then
            echo "$name 已停止"
            return 0
        fi
        sleep 1
    done
    echo "警告：$name :$port ${grace}s 内仍在监听，请人工检查（日志 $LOG_DIR/dev-*.log）" >&2
    return 1
}

cmd_down() {
    local rc=0
    # 先停 Worker（停止领新任务并上报在途结果），再停后端与前端
    stop_port "$WORKER_PORT" "Worker" 35 || rc=1
    stop_port "$BACKEND_PORT" "后端" 15 || rc=1
    stop_port "$FRONTEND_PORT" "前端" 10 || rc=1
    return "$rc"
}

status_line() {
    local name="$1" port="$2" url="$3"
    if port_listening "$port"; then
        echo "  [运行中] $name  $url"
    else
        echo "  [未运行] $name  $url"
    fi
}

cmd_status() {
    echo "开发环境状态："
    status_line "后端 API      " "$BACKEND_PORT" "http://127.0.0.1:$BACKEND_PORT"
    status_line "前端控制台    " "$FRONTEND_PORT" "http://127.0.0.1:$FRONTEND_PORT"
    status_line "Worker 控制台 " "$WORKER_PORT" "http://127.0.0.1:$WORKER_PORT"
    # 本地对象存储由 docker compose 托管（dev-up 按 local-s3-decide.sh 决策带起）；
    # docker 缺失时跳过该行。compose 文件与 up 保持一致（含 compose.local.yaml
    # 覆盖），URL 尊重端口映射的绑定地址 AGENT_LEGION_S3_BIND（默认 127.0.0.1）。
    if command -v docker >/dev/null 2>&1; then
        local compose_files=(-f deploy/compose.host.yaml)
        [[ -f deploy/compose.local.yaml ]] && compose_files+=(-f deploy/compose.local.yaml)
        local bind configured running label url state
        bind="$(read_env_value AGENT_LEGION_S3_BIND || true)"
        bind="${bind:-127.0.0.1}"
        configured="$(scripts/local-s3-decide.sh --service-name .env 2>/dev/null || echo seaweedfs)"
        case "$configured" in
            rustfs) label="RustFS"; url="http://${bind}:9000（console :9001）" ;;
            *) label="SeaweedFS"; url="http://${bind}:8333（master UI :9333）" ;;
        esac
        # 按实际运行的容器报告（系统性评审 #346：配置 rustfs 但 seaweedfs
        # 在跑时，不能打印一个不存在的「运行中 rustfs」）。
        running="$(docker compose "${compose_files[@]}" ps --status running --services 2>/dev/null \
            | grep -xE 'rustfs|seaweedfs' || true)"
        if [[ -n "$running" ]]; then
            state="运行中"
            # 配置与实际不一致（另一后端在跑）时如实点名。
            if ! grep -qx "$configured" <<<"$running"; then
                state="运行中(注意: 实际为 ${running//$'\n'/,}，与配置 ${configured} 不一致)"
            fi
        else
            state="未运行"
        fi
        echo "  [${state}] ${label}        ${url}"
    fi
    echo "  日志：$LOG_DIR/dev-{backend,frontend,worker}.log"
}

case "${1:-}" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    *)
        echo "用法: $0 {up|down|status}" >&2
        exit 2
        ;;
esac
