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
#   - 测试库: agent_legion_test_<开发库后缀小写化>；tower 子实例改用
#     <父worktree名>_<子名> 作为后缀（tests/postgres_support.py 同规则）
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

# tower 子 worktree 使用 <父 worktree>/.tower/worktrees/wt-* 布局，复用父
# 开发库/bucket；其父路径仅由这个严格布局得出，调用方仍需核验父 worktree
# 已注册。非 tower 路径输出空字符串。
worktree_tower_parent() {
    local path="$1" name container parent
    name="${path##*/}"
    container="${path%/*}"
    [[ "$name" == wt-?* && "${container##*/}" == "worktrees" ]] || return 0
    parent="${container%/*}"
    [[ "${parent##*/}" == ".tower" ]] || return 0
    parent="${parent%/*}"
    [[ -n "$parent" && "$parent" != "$path" ]] || return 0
    printf '%s\n' "$parent"
    return 0
}

# 测试库始终按当前 worktree 独立派生；tower 子实例把父名纳入标识，避免
# 不同父实例下的同名 wt-* 共用测试库。与 tests/postgres_support.py 同规则。
worktree_derived_test_db_for_path() {
    local path="$1" parent
    parent="$(worktree_tower_parent "$path")"
    if [[ -n "$parent" ]]; then
        worktree_derived_test_db "$(basename "$parent")_$(basename "$path")"
    else
        worktree_derived_test_db "$(basename "$path")"
    fi
}

# 列出仍注册且目录存在的 tower 子 worktree（每行一个绝对路径）。git 查询
# 失败返回 2，删除护栏必须把它当作无法确认而拒绝继续。
worktree_tower_dependents() {
    local repo="$1" parent="$2" listing path
    listing="$(cd "$repo" && git worktree list --porcelain)" || return 2
    while IFS= read -r path; do
        [[ -d "$path" ]] || continue
        if [[ "$(worktree_tower_parent "$path")" == "$parent" ]]; then
            printf '%s\n' "$path"
        fi
    done <<EOF
$(printf '%s\n' "$listing" | awk '/^worktree /{print substr($0, 10)}')
EOF
    return 0
}

# 父 worktree 的派生库/bucket 被 tower 子 worktree 复用；仍有子实例时拒绝
# 清理父资源。ACTION 是给人看的动作描述。
worktree_require_no_tower_dependents() {
    local repo="$1" parent="$2" action="$3" dependents rc=0
    dependents="$(worktree_tower_dependents "$repo" "$parent")" || rc=$?
    if [[ "$rc" -ne 0 ]]; then
        echo "错误: 无法列出已注册 worktree（git worktree list 失败），无法确认 tower 子 worktree 状态，拒绝${action}。" >&2
        return 1
    fi
    [[ -n "$dependents" ]] || return 0
    echo "错误: worktree '${parent}' 仍有 tower 子 worktree 复用其派生库/bucket，拒绝${action}:" >&2
    printf '%s\n' "$dependents" | while IFS= read -r dependent; do
        echo "        ${dependent}" >&2
    done
    echo "      请先由 tower 清理这些子 worktree，再重试。" >&2
    return 1
}

# 初始化完成标记（#1084 R5）：init-worktree.sh 在锁内、派生资源建完后写
# <worktree>/.worktree-initialized.ok（已加入 .gitignore，不阻塞
# git worktree remove，随 worktree 目录一起消失）。删除护栏据此区分同名
# 登记的身份。
WORKTREE_INITIALIZED_MARKER=".worktree-initialized.ok"

# 同名登记是否为派生资源主（仅 INCLUDE_SELF=1 的删除护栏使用；别名碰撞
# 登记同样经它判定——foo_bar 与 foo-bar 归一派生同一库名，见 #950）：
#   - 有完成标记 → 是（健康实例），拒绝删；
#   - 无标记、.env 不存在或不含自身派生库 URL → 待初始化实例：init 在取锁
#     后才碰资源（复制 .env、改写派生 URL、createdb、建 bucket 全在锁内），
#     此刻它不拥有任何资源，不拒绝；
#   - 无标记但 .env 已指向自身派生库（`export ` 前缀形式同样认）→ 本机制
#     引入前创建的存量实例，或 init 在标记写入前崩溃的实例——视作资源主，
#     拒绝删（fail-closed 保护存量；确认其未完成初始化要删时，先移除该
#     登记或手动删资源）；
#   - 严格匹配不认的手改形态（引号包裹/行内注释等）由宽松兜底兜住：.env
#     里任何包含派生库名的行都判资源主（fail-closed）；
#   - 两种匹配都不中（.env 完全没提该库）→ 待初始化实例 → 不拒绝；
#   - .env 存在但不可读 → 无法判定，fail-closed 当资源主。
worktree_registration_owns_resources() {
    local wt_path="$1" wt_name="$2" db
    [[ -f "$wt_path/$WORKTREE_INITIALIZED_MARKER" ]] && return 0
    [[ -e "$wt_path/.env" ]] || return 1
    [[ -r "$wt_path/.env" ]] || return 0
    db="$(worktree_derived_db "$wt_name")"
    # 严格形态（行首锚定 + 可选 export 前缀 + 行尾锚定；派生库名只含
    # [a-zA-Z0-9_]，正则安全）：区分「指向派生库 vs 未指向」。
    if grep -qE "^(export )?AGENT_LEGION_DATABASE_URL=postgresql://127\.0\.0\.1:5432/${db}\$" "$wt_path/.env" 2>/dev/null; then
        return 0
    fi
    # 宽松兜底（fail-closed）：严格形态不认的手改（引号包裹/行内注释等），
    # 只要 .env 有任何一行包含派生库名就判资源主。
    if grep -qF "$db" "$wt_path/.env" 2>/dev/null; then
        return 0
    fi
    return 1
}

# 列出与 WT 派生出同名资源的其他已注册 worktree，每行
# `<worktree名><TAB><撞名的资源名>`（一个 worktree 撞多个资源则多行）。
# 只看目录仍存在的 worktree（目录已不在的 prunable 条目没有使用方）；跳过
# 主仓库根与目标自身。tower 子 worktree 只豁免开发库/bucket，其测试库仍
# 按完整路径（含父名）参与撞名检查；WT_PATH 用于 tower 目标自身。
# INCLUDE_SELF=1 时同名条目也算冲突——普通 worktree 只认「已完成初始化的
# 登记」为资源主（#1084）；tower 没有独立开发资源标记，但其测试库仍须保留
# 检查，不能用开发库/.env 的归属判定把它整体跳过。
# `git worktree list` 失败返回 2（调用方必须当作「无法确认」拒绝继续）。
#   用法: worktree_derived_name_conflicts REPO_DIR WT [INCLUDE_SELF] [WT_PATH]
worktree_derived_name_conflicts() {
    local repo="$1" wt="$2" include_self="${3:-0}" wt_path="${4:-}"
    local listing path other first=1 db test_db bucket tower_parent target_tower_parent=""
    listing="$(cd "$repo" && git worktree list --porcelain)" || return 2
    db="$(worktree_derived_db "$wt")"
    if [[ -n "$wt_path" ]]; then
        test_db="$(worktree_derived_test_db_for_path "$wt_path")"
        target_tower_parent="$(worktree_tower_parent "$wt_path")"
    else
        test_db="$(worktree_derived_test_db "$wt")"
    fi
    bucket="$(worktree_derived_bucket "$wt")"
    while IFS= read -r path; do
        if [[ "$first" -eq 1 ]]; then
            first=0
            continue
        fi
        [[ -d "$path" ]] || continue
        other="$(basename "$path")"
        if [[ "$include_self" != "1" ]]; then
            if [[ -n "$wt_path" && "$path" == "$wt_path" ]]; then
                continue
            elif [[ -z "$wt_path" && "$other" == "$wt" ]]; then
                continue
            fi
        fi
        tower_parent="$(worktree_tower_parent "$path")"
        if [[ "$include_self" == "1" && -z "$tower_parent" ]]; then
            worktree_registration_owns_resources "$path" "$other" || continue
        fi
        if [[ -z "$target_tower_parent" && -z "$tower_parent" ]]; then
            if [[ "$(worktree_derived_db "$other")" == "$db" ]]; then
                printf '%s\t%s\n' "$other" "$db"
            fi
            if [[ "$(worktree_derived_bucket "$other")" == "$bucket" ]]; then
                printf '%s\t%s\n' "$other" "$bucket"
            fi
        fi
        if [[ "$(worktree_derived_test_db_for_path "$path")" == "$test_db" ]]; then
            printf '%s\t%s\n' "$other" "$test_db"
        fi
    done <<EOF
$(printf '%s\n' "$listing" | awk '/^worktree /{print substr($0, 10)}')
EOF
    return 0
}

# 存在派生名冲突（或无法列出 worktree）时向 stderr 打印原因并返回 1。
# ACTION 是给人看的动作描述（如「建库/建 bucket」「删除派生库」）。
# INCLUDE_SELF=1（删除派生资源前的权威检查）：同名 worktree 仍/又注册着
# 也拒绝，见 worktree_derived_name_conflicts。WT_PATH 仅 tower init 传入。
#   用法: worktree_require_unique_derived_names REPO_DIR WT ACTION [INCLUDE_SELF] [WT_PATH]
worktree_require_unique_derived_names() {
    local repo="$1" wt="$2" action="$3" include_self="${4:-0}" wt_path="${5:-}" conflicts rc=0
    conflicts="$(worktree_derived_name_conflicts "$repo" "$wt" "$include_self" "$wt_path")" || rc=$?
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
    if [[ "$include_self" == "1" ]] \
        && printf '%s\n' "$conflicts" | awk -F '\t' -v w="$wt" '$1 == w { found = 1 } END { exit !found }'; then
        echo "      列出的 '${wt}' 是同名 worktree 本身：它仍（或已被重建后又）注册着且已完成初始化，" >&2
        echo "      派生资源归它所有；确认要删就先移除该 worktree（scripts/clean-worktree.sh ${wt}）。" >&2
        echo "      （无初始化完成标记且 .env 未指向派生库的同名登记视为待初始化实例，不构成冲突，#1084）" >&2
    fi
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
# 锁生命周期 vs 进程树（R5 熔断收窄）：持锁进程同步执行全部建/删步骤——
# 锁内没有嵌套或后台执行者，holder 的 pid 就是执行者本身，pid 判活即锁存
# 活。残余窗口只有「持有者被 SIGKILL 时在途的那一条命令会执行完」（任何
# 锁设计都无法撤回在途命令）；被杀后没有任何进程会继续推进后续步骤。
# 判活在 kill -0 之外核验进程身份（#1251）：stat 为 Z 的 zombie（父进程未
# 回收）与 lstart 启动时间不符的 pid 复用都判死，holder 发布时写入自身
# lstart 供比对。无 start= 行的 holder 是旧版本脚本留下的 legacy 登记，
# 回落到 kill-0 判活（宁可活性受损，不在混合版本窗口破坏互斥）。
# 例外收窄到唯一的多步前台子进程：clean 的 bucket delete（uv run python
# 内部是多批 S3 调用）——它拿到 holder 文件路径 + 持有者 pid 作为租约，
# 每个破坏性步骤前校验二者仍有效，失效即自绝（见 clean-worktree.sh）。
#
# 调用方约定：
#   - 只在「撞名检查 → 建/删派生资源」区间持锁；交互确认一律放在取锁之
#     前，取锁后重新做撞名检查再执行（持锁等用户输入会卡住别的 init）。
#   - 取锁时安装 EXIT/INT/TERM/HUP trap 释放锁（会覆盖调用方的同名
#     trap，三个调用脚本均无自己的 trap）；正常路径可提前调用
#     worktree_name_lock_release。
#   - 锁不跨进程借用：需要持锁执行的操作必须在取锁进程内同步完成
#     （clean 不再转调 drop，两者共用本文件的 worktree_db_* 函数）。

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

# 判活（#1251）：kill -0 之外识别 zombie 与 pid 复用——
#   - ps 查不到该 pid，或 stat 以 Z 开头（zombie，父进程未回收）→ 死；
#   - holder-* 文件带持有者发布时写入的 start=（ps -o lstart=，空格归一）：
#     读不到 start= 但文件可读 → 旧版本脚本的 legacy 登记，回落 kill-0 判活
#     （不破坏混合版本窗口的互斥）；读到但与该 pid 当前 lstart 不符 → pid
#     已被复用 → 死；读取失败（文件正被让位方删除）→ 不计数。
#   - .tmp-* 内容可能写了一半，不做启动时间比对。
_wt_name_lock_file_alive() {
    local file="$1" pid stat recorded current
    pid="$(_wt_name_lock_file_pid "$file")"
    _wt_name_lock_pid_alive "$pid" || return 1
    stat="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d ' ')"
    [[ -n "$stat" && "$stat" != Z* ]] || return 1
    if [[ "${file##*/}" == holder-* ]]; then
        if ! recorded="$(sed -n 's/^start=//p' "$file" 2>/dev/null)"; then
            return 1
        fi
        if [[ -n "$recorded" ]]; then
            current="$(ps -o lstart= -p "$pid" 2>/dev/null | tr -s ' ')"
            [[ -n "$current" && "$recorded" == "$current" ]] || return 1
        fi
    fi
    return 0
}

# 文件名形如 holder-<pid>-<nonce> / .tmp-<pid>-<nonce>，取出 pid。
_wt_name_lock_file_pid() {
    local base="${1##*/}"
    base="${base#holder-}"
    base="${base#.tmp-}"
    printf '%s' "${base%%-*}"
}

# 回收已死的 holder 与发布中途崩溃留下的 .tmp（判活见 _wt_name_lock_file_alive）。
# holder 内容随时可能被其主人删掉（让位）：读失败不计数、不能当错误。
_wt_name_lock_reclaim() {
    local dir="$1" f
    for f in "$dir"/holder-* "$dir"/.tmp-*; do
        [[ -e "$f" ]] || continue
        if ! _wt_name_lock_file_alive "$f"; then
            rm -f "$f"
        fi
    done
    return 0
}

_wt_name_lock_live_count() {
    local dir="$1" f count=0
    for f in "$dir"/holder-*; do
        [[ -e "$f" ]] || continue
        if _wt_name_lock_file_alive "$f"; then
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
    _WT_NAME_LOCK_OWNED=0
    return 0
}

# 当前进程持有的锁的 holder 文件路径（未持锁返回 1）。供持有者把租约身份
# 传给前台子进程做逐批校验（#1084 熔断重估 R2）：holder 文件名编码
# pid+nonce，删除即租约失效。
worktree_name_lock_lease_file() {
    if [[ "$_WT_NAME_LOCK_OWNED" -eq 1 && -n "$_WT_NAME_LOCK_SLOT" ]]; then
        printf '%s\n' "$_WT_NAME_LOCK_SLOT"
        return 0
    fi
    return 1
}

# 释放本进程持有的锁并撤掉 trap。
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
    if ! dir="$(worktree_name_lock_dir "$repo")" || ! mkdir -p "$dir" 2>/dev/null; then
        echo "错误: 无法定位或创建派生名锁目录（git common dir 不可用），拒绝${what}（#1084）。" >&2
        return 1
    fi
    poll="${AGENT_LEGION_WT_NAME_LOCK_POLL_SECONDS:-1}"
    [[ "$poll" =~ ^[0-9]+$ && "$poll" -gt 0 ]] || poll=1
    # holder 携带本进程启动时间（#1251 判活核验 pid 复用）；ps 读不到就拒绝
    # ——发布无法核验身份的 holder 会让竞争者把它当死锁回收，破坏互斥。
    local start
    start="$(ps -o lstart= -p $$ 2>/dev/null | tr -s ' ')"
    if [[ -z "$start" ]]; then
        echo "错误: 无法读取本进程启动时间（ps 不可用），拒绝${what}（#1251）。" >&2
        return 1
    fi
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
        if ! printf 'pid=%s %s（%s，%s 起）\nstart=%s\n' "$$" "$what" "$(pwd)" \
            "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$start" >"$_WT_NAME_LOCK_TMP" \
            || ! mv "$_WT_NAME_LOCK_TMP" "$_WT_NAME_LOCK_SLOT"; then
            _wt_name_lock_release
            echo "错误: 写入派生名锁失败（${dir}），拒绝${what}（#1084）。" >&2
            return 1
        fi
        _WT_NAME_LOCK_TMP=""
        if [[ "$(_wt_name_lock_live_count "$dir")" -eq 1 ]]; then
            _WT_NAME_LOCK_OWNED=1
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


# --- 派生库探测与删除（clean-worktree.sh 与 drop-worktree-db.sh 共用）-------
# R5 熔断收窄后 clean 不再转调 drop 子进程：两个入口各自在自己的持锁进程内
# 同步执行删除，锁内没有嵌套/后台执行者。调用方须先自行取派生名锁
# （drop 的 --list 计划阶段除外，只读）。
# psql 探测失败（连接级错误）一律 fail-closed：「查询失败」不等于「库不
# 存在」（#1084）。

# psql 存在性检查 + dev role 连接护栏（存在 agent_legion_dev 就以它连接——
# 非 superuser、只拥有派生库，对共享/prod 库的 DROP 会被 Postgres 拒绝）。
worktree_db_prepare() {
    command -v psql >/dev/null 2>&1 || { echo "错误: 未找到 psql" >&2; return 1; }
    local dev_role="agent_legion_dev"
    if psql -d postgres -tAc "select 1 from pg_roles where rolname='$dev_role'" 2>/dev/null | grep -q 1; then
        export PGUSER="$dev_role"
    else
        echo "提示: 集群无 $dev_role role，以当前用户（$(whoami)）连接——仅靠名字护栏保护。" >&2
    fi
    return 0
}

# 收集当前存在的目标库（owner + 大小）。$2 为 "print" 时打印每个库的
# 待删除/不存在行。结果在 _WT_DB_FOUND / _WT_DB_LIST（空格分隔；库名由
# 派生规则保证不含空白）。
worktree_db_collect() {
    local wt="$1" verbose="${2:-}" db row
    _WT_DB_FOUND=0
    _WT_DB_LIST=""
    for db in "$(worktree_derived_db "$wt")" "$(worktree_derived_test_db "$wt")"; do
        if ! row="$(psql -d postgres -tAc \
            "select r.rolname || '|' || pg_size_pretty(pg_database_size(d.datname)) from pg_database d join pg_roles r on r.oid=d.datdba where d.datname='$db'" \
            2>/dev/null)"; then
            echo "错误: 探测库 ${db} 失败（Postgres 不可达？），无法确认目标库状态，中止（#1084）。" >&2
            echo "      请在数据库恢复后重跑本脚本。" >&2
            return 1
        fi
        if [[ -n "$row" ]]; then
            _WT_DB_FOUND=$((_WT_DB_FOUND + 1))
            _WT_DB_LIST="${_WT_DB_LIST} ${db}"
            [[ "$verbose" == "print" ]] && echo "待删除: $db (owner=${row%%|*}, size=${row##*|})"
        else
            [[ "$verbose" == "print" ]] && echo "不存在（跳过）: $db"
        fi
    done
    return 0
}

# 逐库复核存在性后 dropdb（复核失败 fail-closed；只删 worktree_db_collect
# 锁内重探测到的集合）。
worktree_db_drop() {
    local db exists
    for db in $_WT_DB_LIST; do
        if ! exists="$(psql -d postgres -tAc "select 1 from pg_database where datname='$db'" 2>/dev/null)"; then
            echo "错误: 删除前复核库 ${db} 失败（Postgres 不可达？），中止（#1084）。" >&2
            return 1
        fi
        if [[ "$exists" == "1" ]]; then
            # 不用 --if-exists：存在性已显式检查，dropdb 的任何报错（含
            # 「must be owner」权限护栏）都应原样失败暴露。
            dropdb "$db" || return 1
            echo "已删除: $db"
        fi
    done
    return 0
}
