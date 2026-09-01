#!/usr/bin/env bash
# 本地材料对象存储（S3 兼容）启停决策，供所有 prod-up/stack 入口共用：
#   - scripts/native-prod-up.sh（原生形态）
#   - scripts/stack-prod-up.sh（make prod-up docker）
#   - Makefile stack-host-up（--compose-flags 内联形态）
#
# 后端选择 AGENT_LEGION_LOCAL_S3_BACKEND=seaweedfs|rustfs（默认 seaweedfs，
# #340 调研后切换默认：SeaweedFS 结构性不存在 RustFS 的全量巡检空转问题）：
#   seaweedfs  compose 的 seaweedfs 服务，profile materials-local（S3 口 8333）
#   rustfs     compose 的 rustfs 服务（逃生舱，存量卷迁移期使用），profile
#              materials-local-rustfs（S3 口 9000）
#
# 三态开关 AGENT_LEGION_LOCAL_S3=auto|always|never（默认 auto）：
#   always  无条件 start（旧版行为）
#   never   无条件 skip（用户使用外部对象存储）
#   auto    按 S3 配置判断：
#             - endpoint 指向本机（127.0.0.1 / localhost / ::1 / compose 内部
#               名 seaweedfs / rustfs）→ start
#             - endpoint 指向外部地址 → skip
#             - endpoint 键出现但显式置空（AGENT_LEGION_S3_ENDPOINT=，AWS S3
#               默认端点写法，与 compose ${VAR-default} 语义一致）→ skip，
#               不再套用编排层注入的 --default-endpoint
#             - 未配 endpoint 但配了 bucket 或凭据（AWS S3 默认端点写法）→ skip
#             - 完全未配置 S3 → start（零配置默认后端；此时凭据缺失不做硬校验，
#               容器只绑 loopback、后端未配 bucket 时材料 API 降级 503，
#               补齐 AGENT_LEGION_S3_* 后重启即启用）
#
# stdout 只输出决策词（start / skip；--compose-flags 时分别为
# "--profile <backend 的 profile>" 与空行），判断原因一律写 stderr。
# 退出码：0 决策完成；2 用法或开关值非法；3 决策为 start 但
# AGENT_LEGION_S3_ACCESS_KEY/SECRET_KEY 未配齐（已显式配置本地存储意图时
# 缺凭据视为配置错误；两个后端留空凭据都会回落镜像默认的公开凭据，必须拦住）。
# auto 误判不会静默失败：后端启动自检（server/app/storage/probe.py 的
# DEGRADED 日志）与 /api/health 的 storage.reachable 会暴露。
#
# 用法: local-s3-decide.sh [--default-endpoint URL] [--compose-flags] [--service-name] [ENV_FILE...]
#   ENV_FILE 按优先级从高到低传入（同一键先出现的文件生效，与 dotenv 一致）；
#   进程环境变量优先于所有文件；文件不存在按空处理。
#   --default-endpoint 模拟编排层注入的 endpoint：docker stack 形态下
#   compose.host.yaml 给 host 默认注入 http://seaweedfs:8333，调用方需如实传入。
#   --service-name 只输出所选后端的 compose 服务名（seaweedfs/rustfs），
#   不做启停决策——供 native-prod-up 等入口直接 up -d <服务名>。
set -euo pipefail

PROFILE_RUSTFS="materials-local-rustfs"
PROFILE_SEAWEEDFS="materials-local"
DEFAULT_ENDPOINT=""
COMPOSE_FLAGS=false
SERVICE_NAME_ONLY=false
ENV_FILES=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --default-endpoint)
            [[ $# -ge 2 ]] || { echo "--default-endpoint 需要参数" >&2; exit 2; }
            DEFAULT_ENDPOINT="$2"
            shift 2
            ;;
        --compose-flags)
            COMPOSE_FLAGS=true
            shift
            ;;
        --service-name)
            # 只输出所选后端的 compose 服务名（seaweedfs / rustfs）后退出，
            # 供 native-prod-up 等入口直接 up -d <服务名>；不输出决策。
            SERVICE_NAME_ONLY=true
            shift
            ;;
        -*)
            echo "未知参数: $1" >&2
            exit 2
            ;;
        *)
            ENV_FILES+=("$1")
            shift
            ;;
    esac
done

# 取值优先级：进程环境 > 先出现的 env 文件；空值按未配置处理。
lookup() {
    local key="$1" value file line
    value="$(printenv "$key" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
        printf '%s' "$value"
        return 0
    fi
    for file in ${ENV_FILES[@]+"${ENV_FILES[@]}"}; do
        [[ -f "$file" ]] || continue
        line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null | head -n 1 || true)"
        [[ -n "$line" ]] || continue
        value="$(_dotenv_value "$line")"
        if [[ -n "$value" ]]; then
            printf '%s' "$value"
            return 0
        fi
    done
    return 0
}

# 解析 env 行的值部分：去 = 前缀、首尾空白与一层配对的引号（与 dotenv 对齐）。
# 注意：这是仓库里第二份 shell dotenv 解析（另一份是 scripts/dev_stack.sh 的
# read_env_value，语义更窄、仅够读扁平 KEY=VALUE 凭据）。需要扩展语义时不要
# 各自漂移，应合并为一份实现。
_dotenv_value() {
    local value="${1#*=}"
    value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    if [[ ${#value} -ge 2 && "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
    elif [[ ${#value} -ge 2 && "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
}

# endpoint 专用，严格 dotenv 语义：按优先级（进程环境 > 文件按传入顺序）
# 找第一个出现该键的来源并用它的值（哪怕为空）——空值也是值，不回退更低
# 优先级来源；完全未出现返回 1。bucket/凭据/开关保持空=未配置，仍走 lookup。
lookup_first() {
    local key="$1" file line
    if printenv "$key" >/dev/null 2>&1; then
        printenv "$key"
        return 0
    fi
    for file in ${ENV_FILES[@]+"${ENV_FILES[@]}"}; do
        [[ -f "$file" ]] || continue
        line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null | head -n 1 || true)"
        [[ -n "$line" ]] || continue
        _dotenv_value "$line"
        return 0
    done
    return 1
}

endpoint_is_local() {
    local host="$1"
    host="${host#*://}"      # 去 scheme
    host="${host%%/*}"       # 去 path
    host="${host##*@}"       # 去 userinfo
    if [[ "$host" == \[* ]]; then
        host="${host#\[}"    # IPv6 字面量 [::1]:9000
        host="${host%%]*}"
    else
        host="${host%%:*}"   # 去端口
    fi
    host="$(printf '%s' "$host" | tr 'A-Z' 'a-z')"
    case "$host" in
        127.0.0.1 | localhost | ::1 | seaweedfs | rustfs) return 0 ;;
        *) return 1 ;;
    esac
}

# backend/endpoint 指向不一致时给明确指引（不阻断：老配置里 endpoint 指向
# rustfs/:9000 而 BACKEND 默认已是 seaweedfs 的存量用户，正是要被提示引导）。
# host 提取与 endpoint_is_local 同款（去 scheme/path/userinfo/IPv6 括号），
# 端口参与判断——127.0.0.1:9000 与 rustfs:9000 都指向 rustfs 形态的端口。
warn_backend_mismatch() {
    local hostport="$1" host port
    hostport="${hostport#*://}"; hostport="${hostport%%/*}"
    hostport="${hostport##*@}"
    if [[ "$hostport" == \[* ]]; then
        host="${hostport#\[}"; host="${host%%]*}"
        port="${hostport##*:}"
    else
        host="${hostport%%:*}"
        port="${hostport##*:}"
        [[ "$port" == "$hostport" ]] && port=""
    fi
    host="$(printf '%s' "$host" | tr 'A-Z' 'a-z')"
    local points_at=""
    case "$host" in
        rustfs) points_at=rustfs ;;
        seaweedfs) points_at=seaweedfs ;;
        127.0.0.1 | localhost | ::1)
            case "$port" in
                9000) points_at=rustfs ;;
                8333) points_at=seaweedfs ;;
            esac
            ;;
    esac
    if [[ "$points_at" == "rustfs" && "$BACKEND" == "seaweedfs" ]]; then
        echo "提示: AGENT_LEGION_S3_ENDPOINT 指向 rustfs 形态地址（${hostport}）但 BACKEND=seaweedfs——" >&2
        echo "      存量 rustfs 用户请显式配 AGENT_LEGION_LOCAL_S3_BACKEND=rustfs（endpoint 保持 :9000）" >&2
        echo "      （或迁移到 seaweedfs 并把 endpoint 改为 :8333，见 docs/materials-storage-deployment.md）" >&2
    elif [[ "$points_at" == "seaweedfs" && "$BACKEND" == "rustfs" ]]; then
        echo "提示: AGENT_LEGION_S3_ENDPOINT 指向 seaweedfs 形态地址（${hostport}）但 BACKEND=rustfs——" >&2
        echo "      请修正 AGENT_LEGION_LOCAL_S3_BACKEND 或 endpoint 之一" >&2
    fi
}

# 解析后端选择（默认 seaweedfs）。LOCAL_S3=never 时无需校验——外部对象
# 存储形态下本地后端不启动，backend 值无意义（非法值也不该 fail-fast 一个
# 与用户无关的开关，系统性评审 #346）。--service-name 始终校验：它的唯一
# 使命就是输出合法服务名。
BACKEND="$(lookup AGENT_LEGION_LOCAL_S3_BACKEND)"
BACKEND="${BACKEND:-seaweedfs}"
validate_backend() {
    case "$BACKEND" in
        seaweedfs) PROFILE="$PROFILE_SEAWEEDFS" ;;
        rustfs) PROFILE="$PROFILE_RUSTFS" ;;
        *)
            echo "错误: AGENT_LEGION_LOCAL_S3_BACKEND=${BACKEND} 非法，只支持 seaweedfs|rustfs" >&2
            exit 2
            ;;
    esac
}
if $SERVICE_NAME_ONLY; then
    validate_backend
    printf '%s\n' "$BACKEND"
    exit 0
fi

emit() {
    local decision="$1" reason="$2"
    echo "本地对象存储(${BACKEND}): ${decision} — ${reason}" >&2
    if $COMPOSE_FLAGS; then
        if [[ "$decision" == "start" ]]; then
            printf -- '--profile %s\n' "$PROFILE"
        else
            printf '\n'
        fi
    else
        printf '%s\n' "$decision"
    fi
}

# 已表达本地存储意图（always / endpoint 指向本机）时，凭据缺失按配置错误处理。
require_keys() {
    local access_key secret_key
    access_key="$(lookup AGENT_LEGION_S3_ACCESS_KEY)"
    secret_key="$(lookup AGENT_LEGION_S3_SECRET_KEY)"
    if [[ -z "$access_key" || -z "$secret_key" ]]; then
        echo "错误: 决策为启动本地 ${BACKEND}，但 AGENT_LEGION_S3_ACCESS_KEY/SECRET_KEY 未配齐。" >&2
        echo "本地后端容器需要 root 凭据（留空会回落镜像默认的公开凭据）。请在" >&2
        echo "deploy/.env（docker 形态，compose 只读它）与 .env（原生形态后端读取）配置，" >&2
        echo "或改用外部对象存储（远程 AGENT_LEGION_S3_ENDPOINT 或 AGENT_LEGION_LOCAL_S3=never）。" >&2
        exit 3
    fi
}

MODE="$(lookup AGENT_LEGION_LOCAL_S3)"
MODE="${MODE:-auto}"
case "$MODE" in
    always)
        validate_backend
        require_keys
        emit start "AGENT_LEGION_LOCAL_S3=always"
        ;;
    never)
        # 不校验 backend（见上）；skip 输出也无 profile。
        PROFILE=""
        emit skip "AGENT_LEGION_LOCAL_S3=never（使用外部对象存储）"
        ;;
    auto)
        validate_backend
        endpoint_present=false
        if endpoint="$(lookup_first AGENT_LEGION_S3_ENDPOINT)"; then
            endpoint_present=true
        else
            # 键完全未出现才允许编排层注入的默认 endpoint 兜底。
            endpoint="$DEFAULT_ENDPOINT"
        fi
        if [[ -n "$endpoint" ]]; then
            if endpoint_is_local "$endpoint"; then
                require_keys
                warn_backend_mismatch "$endpoint"
                emit start "AGENT_LEGION_S3_ENDPOINT=${endpoint} 指向本机"
            else
                emit skip "AGENT_LEGION_S3_ENDPOINT=${endpoint} 指向外部地址，使用外部对象存储"
            fi
        elif $endpoint_present; then
            emit skip "AGENT_LEGION_S3_ENDPOINT 显式置空（AWS S3 默认端点写法），使用外部对象存储"
        else
            bucket="$(lookup AGENT_LEGION_S3_BUCKET)"
            access_key="$(lookup AGENT_LEGION_S3_ACCESS_KEY)"
            secret_key="$(lookup AGENT_LEGION_S3_SECRET_KEY)"
            if [[ -n "$bucket" || -n "$access_key" || -n "$secret_key" ]]; then
                emit skip "已配置 AGENT_LEGION_S3_BUCKET/凭据但未配置 endpoint（AWS S3 默认端点写法），使用外部对象存储"
            else
                emit start "未配置外部 S3，使用本地 ${BACKEND}（AGENT_LEGION_S3_* 未配齐前材料 API 降级 503）"
            fi
        fi
        ;;
    *)
        echo "错误: AGENT_LEGION_LOCAL_S3=${MODE} 非法，只支持 auto|always|never" >&2
        exit 2
        ;;
esac
