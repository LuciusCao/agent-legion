#!/usr/bin/env bash
# 删除某个已移除 worktree 的派生 Postgres 库（开发库 + 测试库）。
#
# 防误删护栏（针对共享/prod 库 agent_legion）：
#   1. 目标库名一律由 <worktree名> 派生（与 scripts/init-worktree.sh、
#      tests/postgres_support.py 同一规则），永远带后缀，不可能等于裸
#      agent_legion / postgres / template*；
#   2. 集群里存在 agent_legion_dev role 时以它连接（非 superuser、只拥有
#      派生库）——此时对任何非派生库的 DROP 会被 Postgres 直接拒绝，
#      不依赖调用者小心；
#   3. 执行前打印每个目标库的 owner 与大小，交互确认（--yes 跳过）。
#
# 用法: scripts/drop-worktree-db.sh <worktree名> [--yes]
set -euo pipefail

usage() {
    echo "用法: $0 <worktree名> [--yes]" >&2
    exit 2
}

[[ $# -ge 1 && $# -le 2 ]] || usage
WT="$1"
ASSUME_YES="${2:-}"
[[ -z "$ASSUME_YES" || "$ASSUME_YES" == "--yes" ]] || usage

# 名字校验：只允许 worktree 目录名的合法字符集；派生结果必然带
# `agent_legion_` 前缀加非空后缀，结构上碰不到共享/prod 库。
if [[ ! "$WT" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
    echo "错误: 非法 worktree 名 '$WT'" >&2
    exit 1
fi

NAME="$(printf '%s' "$WT" | tr -c 'a-zA-Z0-9_' '_')"
DB="agent_legion_${NAME}"
# 测试库派生规则（tests/postgres_support.py）额外小写化
TEST_DB="agent_legion_test_$(printf '%s' "$NAME" | tr 'A-Z' 'a-z')"

command -v psql >/dev/null 2>&1 || { echo "错误: 未找到 psql" >&2; exit 1; }

# 护栏 2：存在 dev role 就以它连接（非 superuser，只拥有派生库）。
DEV_ROLE="agent_legion_dev"
if psql -d postgres -tAc "select 1 from pg_roles where rolname='$DEV_ROLE'" 2>/dev/null | grep -q 1; then
    export PGUSER="$DEV_ROLE"
else
    echo "提示: 集群无 $DEV_ROLE role，以当前用户（$(whoami)）连接——仅靠名字护栏保护。" >&2
fi

# 收集存在的目标库（owner + 大小）
FOUND=0
for db in "$DB" "$TEST_DB"; do
    row="$(psql -d postgres -tAc \
        "select r.rolname || '|' || pg_size_pretty(pg_database_size(d.datname)) from pg_database d join pg_roles r on r.oid=d.datdba where d.datname='$db'" \
        2>/dev/null)" || true
    if [[ -n "$row" ]]; then
        echo "待删除: $db (owner=${row%%|*}, size=${row##*|})"
        FOUND=$((FOUND + 1))
    else
        echo "不存在（跳过）: $db"
    fi
done
if [[ "$FOUND" -eq 0 ]]; then
    echo "没有需要删除的库。"
    exit 0
fi

if [[ "$ASSUME_YES" != "--yes" ]]; then
    read -r -p "确认删除以上 $FOUND 个库？输入 worktree 名 '$WT' 确认: " reply
    if [[ "$reply" != "$WT" ]]; then
        echo "已取消。" >&2
        exit 1
    fi
fi

for db in "$DB" "$TEST_DB"; do
    if psql -d postgres -tAc "select 1 from pg_database where datname='$db'" 2>/dev/null | grep -q 1; then
        # 不用 --if-exists：存在性已显式检查，dropdb 的任何报错（含
        # 「must be owner」权限护栏）都应原样失败暴露。
        dropdb "$db"
        echo "已删除: $db"
    fi
done
