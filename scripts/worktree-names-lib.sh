# shellcheck shell=bash
# worktree 名 → 派生资源名的唯一实现与撞名检测（仅供 source，不单独执行）。
#
# 调用方：scripts/init-worktree.sh（建库/建 bucket 前）、
# scripts/clean-worktree.sh 与 scripts/drop-worktree-db.sh（删除前）。
# 派生规则只在这里写一份；clean-worktree.sh 的 S3 heredoc 里有一份 python
# 等价实现，由 tests/scripts/test_clean_worktree_guard.py 钉住两边一致。
#
# 派生规则（存量 worktree 靠它找到自己的库/bucket，不得修改）：
#   - 开发库: agent_legion_<非 [a-zA-Z0-9_] 归并为 '_'>（保留大小写）
#   - 测试库: agent_legion_test_<开发库后缀小写化>（tests/postgres_support.py）
#   - bucket: agent-legion-<小写化后非 [a-z0-9-] 归并为 '-'>
# 映射不是单射（#950）：foo_bar / foo-bar / foo.bar 派生同一开发库，Foo 与
# foo 派生同一测试库与 bucket。规则不改，改为在建/删前检测与其他已注册
# worktree 的派生名冲突并拒绝。
#
# 兼容系统 bash 3.2（macOS）：不用数组，set -u 下无空数组展开问题。

worktree_derived_db() {
    printf 'agent_legion_%s' "$(printf '%s' "$1" | tr -c 'a-zA-Z0-9_' '_')"
}

worktree_derived_test_db() {
    printf 'agent_legion_test_%s' "$(printf '%s' "$1" | tr -c 'a-zA-Z0-9_' '_' | tr 'A-Z' 'a-z')"
}

worktree_derived_bucket() {
    printf 'agent-legion-%s' "$(printf '%s' "$1" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9-' '-')"
}

# 列出与 WT 派生出同名资源的其他已注册 worktree，每行
# `<worktree名><TAB><撞名的资源名>`（一个 worktree 撞多个资源则多行）。
# 只看目录仍存在的 worktree（目录已不在的 prunable 条目没有使用方）；跳过
# 主仓库根（第一个条目，从不初始化派生资源）与同名条目（WT 自身）。
# `git worktree list` 失败返回 2（调用方必须当作「无法确认」拒绝继续）。
#   用法: worktree_derived_name_conflicts REPO_DIR WT
worktree_derived_name_conflicts() {
    local repo="$1" wt="$2" listing path other first=1
    local db test_db bucket
    listing="$(cd "$repo" && git worktree list --porcelain)" || return 2
    db="$(worktree_derived_db "$wt")"
    test_db="$(worktree_derived_test_db "$wt")"
    bucket="$(worktree_derived_bucket "$wt")"
    while IFS= read -r path; do
        if [[ "$first" -eq 1 ]]; then
            first=0
            continue
        fi
        [[ -d "$path" ]] || continue
        other="$(basename "$path")"
        [[ "$other" != "$wt" ]] || continue
        if [[ "$(worktree_derived_db "$other")" == "$db" ]]; then
            printf '%s\t%s\n' "$other" "$db"
        fi
        if [[ "$(worktree_derived_test_db "$other")" == "$test_db" ]]; then
            printf '%s\t%s\n' "$other" "$test_db"
        fi
        if [[ "$(worktree_derived_bucket "$other")" == "$bucket" ]]; then
            printf '%s\t%s\n' "$other" "$bucket"
        fi
    done <<EOF
$(printf '%s\n' "$listing" | awk '/^worktree /{print substr($0, 10)}')
EOF
    return 0
}

# 存在派生名冲突（或无法列出 worktree）时向 stderr 打印原因并返回 1。
# ACTION 是给人看的动作描述（如「建库/建 bucket」「删除派生库」）。
#   用法: worktree_require_unique_derived_names REPO_DIR WT ACTION
worktree_require_unique_derived_names() {
    local repo="$1" wt="$2" action="$3" conflicts rc=0
    conflicts="$(worktree_derived_name_conflicts "$repo" "$wt")" || rc=$?
    if [[ "$rc" -ne 0 ]]; then
        echo "错误: 无法列出已注册 worktree（git worktree list 失败），无法确认派生名不冲突，拒绝${action}。" >&2
        return 1
    fi
    [[ -n "$conflicts" ]] || return 0
    echo "错误: worktree '${wt}' 与其他仍存在的 worktree 派生出同名资源，拒绝${action}（#950）:" >&2
    printf '%s\n' "$conflicts" | while IFS="$(printf '\t')" read -r other resource; do
        echo "        ${other} -> ${resource}" >&2
    done
    echo "      派生规则把非 [a-zA-Z0-9_] 归并为 '_'（库）、小写化后非 [a-z0-9-] 归并为 '-'（bucket），" >&2
    echo "      仅差在 '_' / '-' / '.' 或大小写的名字会共用同一个库/bucket。" >&2
    echo "      请先把其中一个 worktree 改为不冲突的名字（git worktree move 后在其中重跑" >&2
    echo "      scripts/init-worktree.sh 建它自己的库/bucket）再重试。" >&2
    return 1
}
