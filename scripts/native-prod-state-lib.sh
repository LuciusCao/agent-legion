# shellcheck shell=bash
# 原生 prod 运行态记录（#894），由 native-prod-up.sh / native-prod-down.sh
# source。
#
# 问题：down 原先只按「当前配置」的 bind/port 定位实例——运行中的实例
# 监听旧地址时先改 .env 再 down，会误报未运行；随后 up 在新地址再起一套
# 连同一个库的实例（违反单副本约束，见 docs/architecture/deployment.md）。
# 方案：up 把实际起来的实例（PID + bind + port）落到 data/native-prod.state，
# down 以它为准、配置为辅；记录缺失或陈旧时回落按配置定位并提示。
#
# 记录格式：每行 KEY=VALUE，逐键读取、绝不 source；键为
# {BACKEND,WORKER}_{PID,BIND,PORT}。PID 可能复用：任何按记录 kill 之前都
# 经 native_pid_is_instance 重新校验「进程活着 + 命令行带本服务签名与
# --port + 工作目录就是本仓库根」，三者皆中才视为本实例。

NATIVE_STATE_REL="data/native-prod.state"

# 读取记录中某键的值（缺文件/缺键输出空串）。
native_state_get() {
    local file="$1" key="$2"
    [[ -f "$file" ]] || return 0
    sed -n "s/^${key}=//p" "$file" | tail -1
}

# 服务的命令行签名：与 native-prod-up.sh 的启动命令同源。
native_signature() {
    case "$1" in
        backend) echo "server.app.main:create_prod_app" ;;
        worker) echo "worker.service" ;;
        *) return 1 ;;
    esac
}

# pid 是否仍是本仓库根起的 <kind> 服务、且以 --host <bind> --port <port>
# 启动（防 PID 复用误杀；bind 与 port 都核对，PID 与地址必属同一实例）。
native_pid_is_instance() {
    local root="$1" kind="$2" pid="$3" port="$4" bind="$5" marker cmd cwd
    [[ "$pid" =~ ^[0-9]+$ && "$port" =~ ^[0-9]+$ && -n "$bind" ]] || return 1
    marker="$(native_signature "$kind")" || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    cmd="$(ps -ww -o command= -p "$pid" 2>/dev/null)" || return 1
    [[ "$cmd" == *"$marker"* ]] || return 1
    [[ " $cmd " == *" --port $port "* && " $cmd " == *" --host $bind "* ]] || return 1
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    [[ -n "$cwd" ]] || return 1
    [[ "$(cd "$cwd" 2>/dev/null && pwd -P)" == "$(cd "$root" && pwd -P)" ]]
}

# 监听 <bind>:<port> 的 pid（lsof 显示形式与族别过滤同 up/down 的
# listener_display/listener_family：通配显示为 *:port，IPv6 字面量加方括号）。
native_listener_pids() {
    local bind="$1" port="$2" display="$1" family=4 alt=""
    case "$bind" in
        0.0.0.0 | ::) display="*" ;;
        \[*) ;;
        *:*) display="[$bind]" ;;
    esac
    [[ "$bind" == *:* ]] && family=6
    [[ "$bind" == "::" ]] && alt="[::]:${port}"  # Linux 下 IPv6 通配的变体写法
    lsof -nP -a -iTCP:"$port" -i"$family" -sTCP:LISTEN -F pn 2>/dev/null | awk \
        -v target="${display}:${port}" -v alt="$alt" '
        /^p/ { pid = substr($0, 2) }
        /^n/ { name = substr($0, 2); if (name == target || (alt != "" && name == alt)) print pid }' \
        | sort -u
}

# 监听 <bind>:<port> 且签名匹配的本实例 <kind> 进程 pid（没有则空）——
# PID 与地址取自同一条监听记录，不会把别的地址上的同类实例配给它。
native_find_instance_pid() {
    local root="$1" kind="$2" bind="$3" port="$4" pid
    for pid in $(native_listener_pids "$bind" "$port"); do
        if native_pid_is_instance "$root" "$kind" "$pid" "$port" "$bind"; then
            echo "$pid"
            return 0
        fi
    done
    return 0
}

# 写记录（原子替换）：参数为 root、backend bind/port、worker bind/port。
# PID 取该 bind:port 上签名匹配的实际监听进程（caffeinate 包装时 $! 不是
# 监听者）；刚 nohup 起来尚未监听时 PID 留空，down 届时按记录地址查找。
native_state_write() {
    local root="$1" bbind="$2" bport="$3" wbind="$4" wport="$5" file tmp
    file="$root/$NATIVE_STATE_REL"
    tmp="$file.tmp.$$"
    mkdir -p "$(dirname "$file")"
    {
        echo "# native-prod-up 运行态记录（#894），native-prod-down 以它定位实例；勿手改"
        echo "BACKEND_PID=$(native_find_instance_pid "$root" backend "$bbind" "$bport")"
        echo "BACKEND_BIND=$bbind"
        echo "BACKEND_PORT=$bport"
        echo "WORKER_PID=$(native_find_instance_pid "$root" worker "$wbind" "$wport")"
        echo "WORKER_BIND=$wbind"
        echo "WORKER_PORT=$wport"
    } >"$tmp"
    mv -f "$tmp" "$file"
}

# 输出记录里仍在运行的 <kind> 实例 pid：先认记录 PID，失效（或为空）则在
# 记录的 bind:port 上按签名找；都不中输出空（= 记录陈旧）。PREFIX 为
# BACKEND / WORKER。
native_state_live_pid() {
    local root="$1" kind="$2" prefix="$3" file pid port bind
    file="$root/$NATIVE_STATE_REL"
    port="$(native_state_get "$file" "${prefix}_PORT")"
    bind="$(native_state_get "$file" "${prefix}_BIND")"
    [[ -n "$port" && -n "$bind" ]] || return 0
    pid="$(native_state_get "$file" "${prefix}_PID")"
    if native_pid_is_instance "$root" "$kind" "$pid" "$port" "$bind"; then
        echo "$pid"
        return 0
    fi
    native_find_instance_pid "$root" "$kind" "$bind" "$port"
}
