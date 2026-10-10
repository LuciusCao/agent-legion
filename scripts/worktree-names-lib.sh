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
# 撞名检测是一次性检查，检查到建/删之间可能被另一个 init/clean/drop 插队
# （#1084）：三个入口在「撞名检查 → 建/删派生资源」区间持同一把机器级锁，
# 见文末 worktree_name_lock_acquire。
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

# --- 派生名锁（#1084）--------------------------------------------------------
# 机器级互斥：<git common dir>/worktree-derived-names.lock/ 目录，所有
# worktree 共享同一个 common dir。macOS 没有 flock(1)，沿用
# scripts/gate-queue.sh 的无原语准入：每个竞争者先原子发布自己的 holder
# 文件（写 .tmp 再 mv，文件一出现就有完整内容），再数存活 holder（含自
# 己），恰为 1 才算拿到锁，否则删掉自己的文件、抖动退避后重试。两个竞争者
# 后发布的一方必然数到对方，所以不会同时拿到（最坏双方都让、下一轮再
# 争）；pid 编在文件名里，持有者被 SIGKILL 后由下一个竞争者按 pid 判死
# 回收。与 gate-queue 不同，这里上限固定为 1 且不容忍超额。
#
# 调用方约定：
#   - 只在「撞名检查 → 建/删派生资源」区间持锁；交互确认一律放在取锁之
#     前，取锁后重新做撞名检查再执行（持锁等用户输入会卡住别的 init）。
#   - 取锁时安装 EXIT/INT/TERM/HUP trap 释放锁（会覆盖调用方的同名
#     trap，三个调用脚本均无自己的 trap）；正常路径可提前调用
#     worktree_name_lock_release。
#   - 嵌套：持锁进程导出 AGENT_LEGION_WT_NAME_LOCK_FILE，子进程（如
#     clean 转调 drop）看到它指向的 holder 仍存活即视为已持锁直接返回，
#     且不负责释放——不会自锁死锁，也不会提前放掉父进程的锁。

_WT_NAME_LOCK_OWNED=0
_WT_NAME_LOCK_SLOT=""
_WT_NAME_LOCK_TMP=""

# 锁目录路径（不创建）。git common dir 无法解析时返回 1。
#   用法: worktree_name_lock_dir REPO_DIR
worktree_name_lock_dir() {
    local repo="$1" common
    common="$(cd "$repo" && git rev-parse --git-common-dir 2>/dev/null)" || return 1
    [[ -n "$common" ]] || return 1
    # 相对路径相对于 repo 解析；pwd -P 归一化，保证所有 worktree 算出同一路径。
    common="$(cd "$repo" && cd "$common" 2>/dev/null && pwd -P)" || return 1
    printf '%s/worktree-derived-names.lock\n' "$common"
}

_wt_name_lock_pid_alive() {
    [[ "$1" =~ ^[0-9]+$ ]] && kill -0 "$1" 2>/dev/null
}

# 文件名形如 holder-<pid>-<nonce> / .tmp-<pid>-<nonce>，取出 pid。
_wt_name_lock_file_pid() {
    local base="${1##*/}"
    base="${base#holder-}"
    base="${base#.tmp-}"
    printf '%s' "${base%%-*}"
}

# 回收 pid 已死的 holder 与发布中途崩溃留下的 .tmp。只按文件名判定，不读
# 内容——文件随时可能被其主人删掉（让位），读失败不能当错误。
_wt_name_lock_reclaim() {
    local dir="$1" f
    for f in "$dir"/holder-* "$dir"/.tmp-*; do
        [[ -e "$f" ]] || continue
        if ! _wt_name_lock_pid_alive "$(_wt_name_lock_file_pid "$f")"; then
            rm -f "$f"
        fi
    done
    return 0
}

_wt_name_lock_live_count() {
    local dir="$1" f count=0
    for f in "$dir"/holder-*; do
        [[ -e "$f" ]] || continue
        if _wt_name_lock_pid_alive "$(_wt_name_lock_file_pid "$f")"; then
            count=$((count + 1))
        fi
    done
    echo "$count"
}

_wt_name_lock_describe() {
    local dir="$1" f line out=""
    for f in "$dir"/holder-*; do
        [[ -e "$f" ]] || continue
        [[ "$f" != "$_WT_NAME_LOCK_SLOT" ]] || continue
        line="$(head -n1 "$f" 2>/dev/null)" || continue
        out="${out}${out:+; }${line}"
    done
    printf '%s' "${out:-unknown}"
}

_wt_name_lock_release() {
    if [[ -n "$_WT_NAME_LOCK_TMP" ]]; then
        rm -f "$_WT_NAME_LOCK_TMP"
        _WT_NAME_LOCK_TMP=""
    fi
    if [[ -n "$_WT_NAME_LOCK_SLOT" ]]; then
        rm -f "$_WT_NAME_LOCK_SLOT"
        _WT_NAME_LOCK_SLOT=""
    fi
    if [[ "$_WT_NAME_LOCK_OWNED" -eq 1 ]]; then
        unset AGENT_LEGION_WT_NAME_LOCK_FILE
        _WT_NAME_LOCK_OWNED=0
    fi
    return 0
}

# 释放本进程持有的锁并撤掉 trap；嵌套（未实际持锁）时是 no-op。
worktree_name_lock_release() {
    _wt_name_lock_release
    trap - EXIT INT TERM HUP
}

# 取锁，被占用时阻塞等待并打印持有者（首次立即、之后每 30 秒）。无法定位
# 锁目录时拒绝（返回 1）——无锁继续等于退回 #1084 的竞态。
# WHAT 是给人看的动作描述，写进 holder 文件供等待方展示。
#   用法: worktree_name_lock_acquire REPO_DIR WHAT
worktree_name_lock_acquire() {
    local repo="$1" what="$2" dir poll nonce waited=0 announced=0
    if [[ "$_WT_NAME_LOCK_OWNED" -eq 1 ]]; then
        return 0
    fi
    if [[ -n "${AGENT_LEGION_WT_NAME_LOCK_FILE:-}" && -e "$AGENT_LEGION_WT_NAME_LOCK_FILE" ]] \
        && _wt_name_lock_pid_alive "$(_wt_name_lock_file_pid "$AGENT_LEGION_WT_NAME_LOCK_FILE")"; then
        return 0
    fi
    if ! dir="$(worktree_name_lock_dir "$repo")" || ! mkdir -p "$dir" 2>/dev/null; then
        echo "错误: 无法定位或创建派生名锁目录（git common dir 不可用），拒绝${what}（#1084）。" >&2
        return 1
    fi
    poll="${AGENT_LEGION_WT_NAME_LOCK_POLL_SECONDS:-1}"
    [[ "$poll" =~ ^[0-9]+$ && "$poll" -gt 0 ]] || poll=1
    trap '_wt_name_lock_release' EXIT
    trap '_wt_name_lock_release; exit 130' INT
    trap '_wt_name_lock_release; exit 143' TERM
    trap '_wt_name_lock_release; exit 129' HUP
    while true; do
        _wt_name_lock_reclaim "$dir"
        nonce="$(date +%s)-${RANDOM}${RANDOM}"
        # 先登记路径再写文件：中途被信号打断时 trap 能清掉半成品。
        _WT_NAME_LOCK_TMP="$dir/.tmp-$$-$nonce"
        _WT_NAME_LOCK_SLOT="$dir/holder-$$-$nonce"
        if ! printf 'pid=%s %s（%s，%s 起）\n' "$$" "$what" "$(pwd)" \
            "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >"$_WT_NAME_LOCK_TMP" \
            || ! mv "$_WT_NAME_LOCK_TMP" "$_WT_NAME_LOCK_SLOT"; then
            _wt_name_lock_release
            echo "错误: 写入派生名锁失败（${dir}），拒绝${what}（#1084）。" >&2
            return 1
        fi
        _WT_NAME_LOCK_TMP=""
        if [[ "$(_wt_name_lock_live_count "$dir")" -eq 1 ]]; then
            _WT_NAME_LOCK_OWNED=1
            export AGENT_LEGION_WT_NAME_LOCK_FILE="$_WT_NAME_LOCK_SLOT"
            return 0
        fi
        # 让位：删掉自己的 holder，抖动退避后重试。
        rm -f "$_WT_NAME_LOCK_SLOT"
        _WT_NAME_LOCK_SLOT=""
        if [[ "$announced" -eq 0 ]]; then
            echo "派生名锁被占用，等待中（持有者: $(_wt_name_lock_describe "$dir")）..." >&2
            announced=1
        elif (( waited > 0 && waited % 30 == 0 )); then
            echo "仍在等待派生名锁（已等 ${waited}s，持有者: $(_wt_name_lock_describe "$dir")）..." >&2
        fi
        sleep "${poll}.$(printf '%03d' $((RANDOM % 500)))"
        waited=$((waited + poll))
    done
}
