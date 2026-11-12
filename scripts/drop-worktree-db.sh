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
#      psql 探测失败（连接级错误）fail-closed 中止——「查询失败」不等于
#      「库不存在」，状态未知时不确认、不删除（#1084）；
#   4. 确认之后才取派生名锁（#1084，持锁等用户输入会卡住别的 init），
#      持锁重新做撞名检查与存在性探测，只删锁内实际存在的库——锁前列出的
#      集合仅用于确认展示，确认/等锁期间 init 可能补建库（#1084）。删除前
#      的撞名检查把同名 worktree 本身也算冲突，但只认已完成初始化的登记
#      为资源主（#1084 R5）：无初始化完成标记且 .env 未指向派生库的同名
#      登记是阻塞在锁上的待初始化实例，不拥有资源，不阻断删除——删完后
#      它的 init 在干净石板上自建；
#   5. 父 worktree 仍有复用其派生库的 tower 子 worktree 时拒绝删除（#1409）。
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
MAIN="$(git -C "$SCRIPT_DIR" worktree list --porcelain | awk '/^worktree /&&!seen{print $2; seen=1}')"
TARGET="$MAIN/.worktrees/$WT"

# 护栏 0（#950）：派生名映射不是单射，另一个仍存在的 worktree 派生出同名
# 库时拒绝删除——那是对方正在用的库。这里是不持锁的预检（尽早拒绝、免得白
# 问一次确认），真正的判定在下方取锁之后重做。--list 是只读计划（clean
# 在移除目标之前调用，目标自身此刻合法地注册着），不把同名条目算冲突。
if [[ "$MODE" == "--list" ]]; then
    worktree_require_unique_derived_names "$SCRIPT_DIR" "$WT" "列出派生库" || exit 1
else
    worktree_require_unique_derived_names "$SCRIPT_DIR" "$WT" "删除派生库" 1 || exit 1
fi

worktree_db_prepare

# 计划阶段（不持锁）：列出当前存在的目标库供确认。该集合仅用于确认展示，
# 删除决策以取锁后的重新探测为准（#1084）。
worktree_db_collect "$WT" print
if [[ "$MODE" == "--list" ]]; then
    if [[ "$_WT_DB_FOUND" -eq 0 ]]; then
        echo "没有需要删除的库。"
        exit 0
    fi
    exit 10
fi

if [[ "$_WT_DB_FOUND" -gt 0 && "$MODE" != "--yes" ]]; then
    reply=""
    read -r -p "确认删除以上 $_WT_DB_FOUND 个库？输入 worktree 名 '$WT' 确认: " reply || true
    if [[ "$reply" != "$WT" ]]; then
        echo "已取消。" >&2
        exit 1
    fi
fi

# 护栏 0'（#1084）：确认后取派生名锁并重新检查撞名——确认期间可能有派生
# 名相同（含同名重建）的新 worktree 完成了 init（库名相同，删掉就是删它
# 的库）。持锁后并发的 init 只能在本次删除完成后再建库。锁在脚本退出（含
# 报错/信号）时由 trap 释放。
worktree_name_lock_acquire "$SCRIPT_DIR" "删除派生库 ${WT}" || exit 1
worktree_require_unique_derived_names "$SCRIPT_DIR" "$WT" "删除派生库" 1 || exit 1

# 锁内重新探测（#1084）：确认/等锁期间 init 可能补建了库（或他人已删），
# 只删锁内实际存在的库。
PLANNED_LIST="$_WT_DB_LIST"
worktree_db_collect "$WT"

# 计划外资源比对（#1084 P1-b）：锁内集合比锁前确认计划扩大（出现确认时
# 不存在的新库）时——交互模式中止（这些库用户从未见过、未确认），--yes
# 模式打印警告继续（自动化语义）。集合缩小（期间他人已删）只提示。
EXTRA=""
for db in $_WT_DB_LIST; do
    case "${PLANNED_LIST} " in
        *" ${db} "*) ;;
        *) EXTRA="${EXTRA} ${db}" ;;
    esac
done
if [[ -n "$EXTRA" ]]; then
    if [[ "$MODE" == "--yes" ]]; then
        echo "提示: 锁内重探测发现确认计划外的库:[${EXTRA# }]（等锁期间同名实例可能刚初始化），按 --yes 语义一并删除。" >&2
    else
        echo "错误: 锁内重探测发现确认计划外的库:[${EXTRA# }]——可能同名实例刚初始化（#1084）。" >&2
        echo "      这些库从未展示、未经确认，已中止。请重跑本脚本查看并确认最新计划。" >&2
        exit 1
    fi
elif [[ "$_WT_DB_LIST" != "$PLANNED_LIST" ]]; then
    echo "提示: 锁内重检发现目标库集合与确认时不一致（期间有建/删），以当前实际存在的为准：" >&2
    for db in $_WT_DB_LIST; do
        echo "  锁内待删除: $db" >&2
    done
fi
if [[ "$_WT_DB_FOUND" -eq 0 ]]; then
    echo "没有需要删除的库。"
    exit 0
fi

# tower 子 worktree 复用父实例的库；删除前重查已注册依赖方（#1409）。
worktree_require_no_tower_dependents "$SCRIPT_DIR" "$TARGET" "删除父 worktree 派生库" || exit 1
worktree_db_drop
