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
#   3. 执行前打印每个目标库的 owner 与大小，交互确认（--yes 跳过）；
#   4. 确认之后才取派生名锁（#1084，持锁等用户输入会卡住别的 init），
#      持锁重新做撞名检查与存在性探测，只删锁内实际存在的库——锁前列出的
#      集合仅用于确认展示，确认/等锁期间 init 可能补建库（#1084）。删除前
#      的检查把同名 worktree 本身也算冲突：本脚本只删「已移除」worktree 的
#      库，同名 worktree 仍（或被重建后又）注册着时库归它所有。
#
# 用法: scripts/drop-worktree-db.sh <worktree名> [--yes | --list]
#   --list：只列出存在的目标库后退出（0=没有、10=有待删），不确认、不取锁、
#           不删除；clean-worktree.sh 在移除 worktree 之前用它展示删除计划。
set -euo pipefail

usage() {
    echo "用法: $0 <worktree名> [--yes | --list]" >&2
    exit 2
}

[[ $# -ge 1 && $# -le 2 ]] || usage
WT="$1"
MODE="${2:-}"
[[ -z "$MODE" || "$MODE" == "--yes" || "$MODE" == "--list" ]] || usage

# 名字校验：只允许 worktree 目录名的合法字符集（含 '.'，#587：首字符必须
# 是字母数字，'.'/'..' 进不来）；'.' 在派生时归并为 '_'，结果必然带
# `agent_legion_` 前缀加非空后缀，结构上碰不到共享/prod 库。
if [[ ! "$WT" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]; then
    echo "错误: 非法 worktree 名 '$WT'" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=worktree-names-lib.sh
source "$SCRIPT_DIR/worktree-names-lib.sh"
DB="$(worktree_derived_db "$WT")"
# 测试库派生规则（tests/postgres_support.py）额外小写化
TEST_DB="$(worktree_derived_test_db "$WT")"

# 护栏 0（#950）：派生名映射不是单射，另一个仍存在的 worktree 派生出同名
# 库时拒绝删除——那是对方正在用的库。clean-worktree.sh 转调本脚本前已在
# 移除 worktree 之前做过同一检测（同一共享函数），这里再查一次是独立调用
# 时的兜底，结果一致、开销可忽略。这里是不持锁的预检（尽早拒绝、免得白
# 问一次确认），真正的判定在下方取锁之后重做。--list 是只读计划（clean
# 在移除目标之前调用，目标自身此刻合法地注册着），不把同名条目算冲突。
if [[ "$MODE" == "--list" ]]; then
    worktree_require_unique_derived_names "$SCRIPT_DIR" "$WT" "列出派生库" || exit 1
else
    worktree_require_unique_derived_names "$SCRIPT_DIR" "$WT" "删除派生库" 1 || exit 1
fi

command -v psql >/dev/null 2>&1 || { echo "错误: 未找到 psql" >&2; exit 1; }

# 护栏 2：存在 dev role 就以它连接（非 superuser，只拥有派生库）。
DEV_ROLE="agent_legion_dev"
if psql -d postgres -tAc "select 1 from pg_roles where rolname='$DEV_ROLE'" 2>/dev/null | grep -q 1; then
    export PGUSER="$DEV_ROLE"
else
    echo "提示: 集群无 $DEV_ROLE role，以当前用户（$(whoami)）连接——仅靠名字护栏保护。" >&2
fi

# 收集当前存在的目标库（owner + 大小）。$1 为 "print" 时打印每个库的
# 待删除/不存在行；CONFIRMED 是本次探测到的集合（空格分隔；库名由派生
# 规则保证不含空白）。
collect_existing_dbs() {
    local verbose="${1:-}" db row
    FOUND=0
    CONFIRMED=""
    for db in "$DB" "$TEST_DB"; do
        row="$(psql -d postgres -tAc \
            "select r.rolname || '|' || pg_size_pretty(pg_database_size(d.datname)) from pg_database d join pg_roles r on r.oid=d.datdba where d.datname='$db'" \
            2>/dev/null)" || true
        if [[ -n "$row" ]]; then
            FOUND=$((FOUND + 1))
            CONFIRMED="${CONFIRMED} ${db}"
            [[ "$verbose" == "print" ]] && echo "待删除: $db (owner=${row%%|*}, size=${row##*|})"
        else
            [[ "$verbose" == "print" ]] && echo "不存在（跳过）: $db"
        fi
    done
    return 0
}

# 计划阶段（不持锁）：列出当前存在的目标库供确认。该集合仅用于确认展示，
# 删除决策以取锁后的重新探测为准（#1084）。
collect_existing_dbs print
if [[ "$MODE" == "--list" ]]; then
    if [[ "$FOUND" -eq 0 ]]; then
        echo "没有需要删除的库。"
        exit 0
    fi
    exit 10
fi

if [[ "$FOUND" -gt 0 && "$MODE" != "--yes" ]]; then
    reply=""
    read -r -p "确认删除以上 $FOUND 个库？输入 worktree 名 '$WT' 确认: " reply || true
    if [[ "$reply" != "$WT" ]]; then
        echo "已取消。" >&2
        exit 1
    fi
fi

# 护栏 0'（#1084）：确认后取派生名锁并重新检查撞名——确认期间可能有派生
# 名相同（含同名重建）的新 worktree 完成了 init（库名相同，删掉就是删它
# 的库）。持锁后并发的 init 只能在本次删除完成后再建库。锁在脚本退出（含
# 报错/信号）时由 trap 释放；被 clean-worktree.sh 转调且父进程已持锁时直接
# 复用。
worktree_name_lock_acquire "$SCRIPT_DIR" "删除派生库 ${WT}" || exit 1
worktree_require_unique_derived_names "$SCRIPT_DIR" "$WT" "删除派生库" 1 || exit 1

# 锁内重新探测（#1084）：确认/等锁期间 init 可能补建了库（或他人已删），
# 只删锁内实际存在的库。
PLANNED_CONFIRMED="$CONFIRMED"
collect_existing_dbs
if [[ "$CONFIRMED" != "$PLANNED_CONFIRMED" ]]; then
    echo "提示: 锁内重检发现目标库集合与确认时不一致（期间有建/删），以当前实际存在的为准：" >&2
    for db in $CONFIRMED; do
        echo "  锁内待删除: $db" >&2
    done
fi
if [[ "$FOUND" -eq 0 ]]; then
    echo "没有需要删除的库。"
    exit 0
fi

for db in $CONFIRMED; do
    if psql -d postgres -tAc "select 1 from pg_database where datname='$db'" 2>/dev/null | grep -q 1; then
        # 不用 --if-exists：存在性已显式检查，dropdb 的任何报错（含
        # 「must be owner」权限护栏）都应原样失败暴露。
        dropdb "$db"
        echo "已删除: $db"
    fi
done
