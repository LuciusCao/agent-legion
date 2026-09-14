# 仓库统一的 shell dotenv 解析原语（source 使用）。不设 set -e/-u——shell
# 选项由调用方脚本决定，函数体对两者都安全（无文件参数时 "$@" 展开为空
# 在 set -u 下合法）。
#
# 语义对齐 python-dotenv（override=False）：进程环境已导出的值优先于所有
# 文件（临时覆盖逃生门）；文件按传入顺序取第一个出现的键（先出现的文件
# 生效，后文件不覆盖前文件）。行格式支持 KEY=VALUE 与 export KEY=VALUE，
# 值去首尾空白与一层配对引号（" 或 '）。文件不存在按空处理，键未出现不
# 报错。
#
# 两个原语，差异只在「空值」的处理：
#   dotenv_value <key> <file>...
#       空值按未配置——继续向更低优先级来源找；全部落空输出空串、返回 0
#       （调用方用 ${VAR:-default} 兜底默认值）。原 local-s3-decide.sh 的
#       lookup 语义（开关/凭据/bucket：留空等价未配）。
#   dotenv_value_first <key> <file>...
#       严格 dotenv 语义——第一个出现该键的来源生效（空值也是值，不回退
#       更低优先级来源）；键完全未出现输出空串、返回 1。返回 1 路径须在
#       if 等条件上下文中调用以兼容 set -e。原 local-s3-decide.sh 的
#       lookup_first 语义（endpoint：显式置空是 AWS 默认端点写法，是有
#       效值）。
#
# 消费方：scripts/local-s3-decide.sh（S3 启停决策）、scripts/native-prod-up.sh
# / native-prod-down.sh（NATIVE_* 端口/绑定地址，#486）、scripts/dev_stack.sh
# （S3 凭据/绑定地址）。#486 之前 decide 与 dev_stack 各持一份解析（lookup/
# _dotenv_value 与 read_env_value），本文件是其收敛；新增消费方不得再写
# 下一份。

# 解析单行 `KEY=VALUE`（或 `export KEY=VALUE`）的值部分：去 = 前缀、首尾
# 空白与一层配对引号。
dotenv_parse_line() {
    local value="${1#*=}"
    value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
    if [[ ${#value} -ge 2 && "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
    elif [[ ${#value} -ge 2 && "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
}

# 进程环境 > 文件按传入顺序；空值按未配置继续找；找不到输出空串、返回 0。
dotenv_value() {
    local key="$1"
    shift
    local value file line
    value="$(printenv "$key" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
        printf '%s' "$value"
        return 0
    fi
    for file in "$@"; do
        [[ -f "$file" ]] || continue
        line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null | head -n 1 || true)"
        [[ -n "$line" ]] || continue
        value="$(dotenv_parse_line "$line")"
        if [[ -n "$value" ]]; then
            printf '%s' "$value"
            return 0
        fi
    done
    return 0
}

# 严格 dotenv 语义：第一个出现该键的来源生效（空值也是值）；完全未出现
# 返回 1（条件上下文使用）。
dotenv_value_first() {
    local key="$1"
    shift
    local file line
    if printenv "$key" >/dev/null 2>&1; then
        printenv "$key"
        return 0
    fi
    for file in "$@"; do
        [[ -f "$file" ]] || continue
        line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" 2>/dev/null | head -n 1 || true)"
        [[ -n "$line" ]] || continue
        dotenv_parse_line "$line"
        return 0
    done
    return 1
}
