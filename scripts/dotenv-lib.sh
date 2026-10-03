# shellcheck shell=bash
# 运维 shell 脚本共用的 dotenv 解析（仅供 source，不单独执行）。
#
# 仓库里的 shell 入口（local-s3-decide.sh / dev_stack.sh / native-prod-up.sh /
# native-prod-down.sh）曾各自内嵌一份 KEY=VALUE 解析，语义靠注释互相提醒
# 保持一致（#486 收敛为本文件）。需要扩展语义时只改这里，不要在调用方
# 再写一份。
#
# 解析语义（与 python-dotenv 的常见写法对齐，刻意保持扁平）：
#   - 行匹配 `^[空白]*(export[空白]+)?KEY=`，同一文件取第一个匹配行；
#   - 值部分去首尾空白与一层配对的引号（"..." 或 '...'）；
#   - 不支持多行值、变量插值与行尾注释（运维变量均为扁平值）。
#
# 读取方不依赖 set -e 语义：所有函数在「未找到」时也返回 0（dotenv_line /
# dotenv_lookup_first 除外，见各自说明），调用方按空串判断。

# 解析 env 行的值部分：去 `KEY=` 前缀、首尾空白与一层配对引号。
dotenv_value() {
    local value="${1#*=}"
    value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    if [[ ${#value} -ge 2 && "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
    elif [[ ${#value} -ge 2 && "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
}

# 输出 FILE 中 KEY 的第一个匹配行；文件不存在或无匹配返回 1。
dotenv_line() {
    local key="$1" file="$2" line
    [[ -f "$file" ]] || return 1
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null | head -n 1 || true)"
    [[ -n "$line" ]] || return 1
    printf '%s' "$line"
}

# 取值优先级：进程环境 > 按传入顺序的 env 文件；空值按未配置处理（继续
# 找下一个来源）。全部缺失输出空串，返回 0。
#   用法: dotenv_lookup KEY [FILE...]
dotenv_lookup() {
    local key="$1" value file line
    shift
    value="$(printenv "$key" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
        printf '%s' "$value"
        return 0
    fi
    for file in "$@"; do
        line="$(dotenv_line "$key" "$file")" || continue
        value="$(dotenv_value "$line")"
        if [[ -n "$value" ]]; then
            printf '%s' "$value"
            return 0
        fi
    done
    return 0
}

# 严格 dotenv 语义：按优先级找第一个**出现**该键的来源并用它的值（哪怕
# 为空）——空值也是值，不回退更低优先级来源；完全未出现返回 1。
#   用法: dotenv_lookup_first KEY [FILE...]
dotenv_lookup_first() {
    local key="$1" file line
    shift
    if printenv "$key" >/dev/null 2>&1; then
        printenv "$key"
        return 0
    fi
    for file in "$@"; do
        line="$(dotenv_line "$key" "$file")" || continue
        dotenv_value "$line"
        return 0
    done
    return 1
}

# dotenv_lookup 的带默认值版本：全部来源缺失或为空时输出 DEFAULT。
#   用法: dotenv_lookup_or KEY DEFAULT [FILE...]
dotenv_lookup_or() {
    local key="$1" default="$2" value
    shift 2
    value="$(dotenv_lookup "$key" "$@")"
    printf '%s' "${value:-$default}"
}
