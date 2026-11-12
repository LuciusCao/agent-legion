#!/usr/bin/env bash
# 初始化新 git worktree 的开发环境（幂等，可重复执行）：
#   0. 嵌套防护：普通 worktree 必须是主仓库根的平级子目录；唯一例外是
#      已注册父 worktree 下的 .tower/worktrees/wt-*（复用父资源，#1409）
#   1. 从基准 worktree 复制 .env（若本 worktree 缺失；无法复制则 fail-fast——
#      缺 .env 会让后端回落共享默认库/prod）
#   2. 普通 worktree 把 AGENT_LEGION_DATABASE_URL 指向按名字派生的专属库并尝试建库；
#      tower 子 worktree 保留父配置，不建库/bucket，vault key 也从父实例复制
#      （建库前在锁内做静默收养护栏（#1349）：无初始化完成标记而派生库已存在
#      且非本实例所有时 fail-fast——旧实例清理中断残留的库不得被同名新实例
#      静默沿用）
#   2.2 预暖 .uv-cache：克隆基准 worktree 的 uv 缓存（协议实现在
#       scripts/uv_cache_prewarm.py，失败仅提示并回退冷启动），
#       让首次 uv run 免于从零拉依赖树
#   2.5 按 worktree 名派生 AGENT_LEGION_S3_BUCKET 写入 .env，endpoint 可达时
#       建 bucket 并配置浏览器直传所需的前端 dev origin CORS
#   3. 生成缺失的 deploy/secrets（vault_master_key；worker 全局注册 token 已退役，见 issue #35）
# 用法: scripts/init-worktree.sh [基准 worktree 路径]（默认取第一个非 bare 且非当前的 worktree）
set -euo pipefail

# BSD/GNU sed 对 `-i` 的参数语法不同；带显式 backup suffix 的形式两边
# 都支持。替换成功后删除备份，避免开发配置目录残留 `.bak`。
replace_in_place() {
    local expression="$1"
    local path="$2"
    sed -i.bak -E "$expression" "$path"
    rm -f "${path}.bak"
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=worktree-names-lib.sh
source "$ROOT/scripts/worktree-names-lib.sh"

# 0. 嵌套防护（AGENTS.md §1）：普通 worktree 必须是主仓库根的平级子目录。
#    唯一例外是已注册父 worktree 下的 .tower/worktrees/wt-*（#1409）；
#    它复用父开发库/bucket，但测试库按含父名的独立标识参与撞名检查。
MAIN="$(git worktree list --porcelain | awk '/^worktree /&&!seen{print $2; seen=1}')"
TOWER_PARENT="$(worktree_tower_parent "$ROOT")"
if [[ -n "$TOWER_PARENT" ]]; then
    if ! git worktree list --porcelain | awk -v parent="$TOWER_PARENT" '
        $0 == "worktree " parent { found = 1 }
        END { exit !found }
    '; then
        echo "错误: tower 父 worktree 未注册（${TOWER_PARENT}），拒绝初始化。" >&2
        exit 1
    fi
elif [[ "$ROOT" != "$MAIN" && "$(dirname "$ROOT")" != "$MAIN/.worktrees" ]]; then
    echo "错误: worktree 禁止嵌套（当前: ${ROOT}）。" >&2
    echo "请先 cd 到主仓库根（${MAIN}），再 git worktree add .worktrees/<name> -b <branch> <base>。" >&2
    exit 1
fi

# 在主仓库根本身执行时直接退出（bare 主仓库无工作区，更不会走到这里）
if [[ "$ROOT" == "$MAIN" ]]; then
    echo "当前就是主仓库根，无需初始化。" >&2
    exit 0
fi

BASE="${1:-}"
if [[ -n "$TOWER_PARENT" ]]; then
    if [[ $# -gt 0 && "$BASE" != "$TOWER_PARENT" ]]; then
        echo "错误: tower worktree 只能从父 worktree 复用资源，不能指定其他基准（${BASE}）。" >&2
        exit 1
    fi
    BASE="$TOWER_PARENT"
    # 在任何复制/生成动作前确认父实例已具备可复用配置。缺项时先在父实例
    # 重跑 init，不能给 tower 子实例另建库/bucket 或另生成 vault key。
    if [[ ! -f "$BASE/.env" ]]; then
        echo "错误: tower 父 worktree 缺少 .env（${BASE}/.env）。" >&2
        echo "      请先在父 worktree 运行 scripts/init-worktree.sh，再重试。" >&2
        exit 1
    fi
    for required_key in AGENT_LEGION_DATABASE_URL AGENT_LEGION_S3_BUCKET; do
        if ! grep -qE "^(export )?${required_key}=." "$BASE/.env"; then
            echo "错误: tower 父 worktree 的 .env 缺少 ${required_key}，无法复用派生资源。" >&2
            echo "      请先在父 worktree 运行 scripts/init-worktree.sh，再重试。" >&2
            exit 1
        fi
    done
    if [[ ! -s "$BASE/deploy/secrets/vault_master_key" ]]; then
        echo "错误: tower 父 worktree 缺少 vault_master_key，无法复用父实例 vault。" >&2
        echo "      请先在父 worktree 运行 scripts/init-worktree.sh，再重试。" >&2
        exit 1
    fi
elif [[ -z "$BASE" ]]; then
    # 默认取第一个非 bare、非当前、非主仓库根的 worktree 作基准（主仓库根是
    # bare/无工作区配置，永不适合作基准）。注意选中的基准自身也可能缺 .env，
    # 由下方 fail-fast 兜底（2026-08-18 事故：基准 worktree 缺 .env → 新
    # worktree 无 .env → 后端回落共享默认库即 prod 库）。
    # awk 不提前 exit（#1247，同 #1145 族）：读完全部输入、只打印首个匹配，
    # 否则 worktree 多到 porcelain 输出超过管道缓冲时，上游 git 在 awk 退出后
    # 继续写会收到 SIGPIPE，pipefail 下命令替换整体 141、set -e 静默终止。
    BASE="$(git worktree list --porcelain | awk -v root="$ROOT" -v main="$MAIN" '
        /^worktree / {
            if (!found && wt != "" && !isbare && wt != root && wt != main) { print wt; found=1 }
            wt = substr($0, 10); isbare = 0
        }
        /^bare$/ { isbare = 1 }
        END { if (!found && wt != "" && !isbare && wt != root && wt != main) print wt }
    ')"
fi
if [[ -n "$BASE" && "$BASE" == "$ROOT" ]]; then
    echo "当前就是基准 worktree，无需初始化。" >&2
    exit 0
fi

# 0.5 tower 只豁免开发库/bucket；其独立测试库仍按含父名的路径参与撞名
#     检查（#1409 P2）。检查与后续资源动作统一持派生名锁（#950/#1084）。
worktree_name_lock_acquire "$ROOT" "初始化 $(basename "$ROOT")（建库/建 bucket）" || exit 1
if [[ -n "$TOWER_PARENT" ]]; then
    worktree_require_unique_derived_names "$ROOT" "$(basename "$ROOT")" "初始化（测试库撞名检查）" 0 "$ROOT" || exit 1
else
    worktree_require_unique_derived_names "$ROOT" "$(basename "$ROOT")" "初始化（建库/建 bucket）" || exit 1
fi
NAME="$(worktree_derived_db "$(basename "$ROOT")")"
NAME="${NAME#agent_legion_}"

# 1. .env
# 记录 .env 是否本趟从基准复制：静默收养护栏（#1349）的拒绝路径要移除本趟
# 副本（它指向基准实例的库/bucket），pre-existing .env 则不动（复验 P2-1）。
ENV_COPIED_THIS_RUN=""
if [[ ! -f .env ]]; then
    if [[ -z "$BASE" ]]; then
        echo "警告: 未找到基准 worktree（主仓库为 bare 且无其他 worktree），跳过 .env 复制" >&2
    elif [[ -f "$BASE/.env" ]]; then
        cp "$BASE/.env" .env
        ENV_COPIED_THIS_RUN=1
        echo "已复制 .env <- $BASE"
    else
        echo "警告: $BASE/.env 不存在，跳过 .env 复制" >&2
    fi
fi

# .env 缺失是硬错误：没有它，后端/门禁会回落代码默认的共享库（即 prod 库），
# 启动迁移直接改写 prod（2026-08-18 事故的另一半根因）。fail-fast，不留
# 「警告然后继续」的余地。
if [[ ! -f .env ]]; then
    echo "错误: .env 缺失且无法从基准 worktree 复制（BASE=${BASE:-未找到}）。" >&2
    echo "缺少 .env 时后端会回落共享默认库（prod）——请手工从其他 worktree 复制 .env 后重跑本脚本。" >&2
    exit 1
fi
# .env 含真实凭据（S3 key 等），权限收紧（幂等，与 install-deps.sh 一致）。
chmod 600 .env

# 2. 普通 worktree 建专属 Postgres 库；tower 子实例保留父 .env 中的值。
if [[ -z "$TOWER_PARENT" ]]; then
DB="$(worktree_derived_db "$(basename "$ROOT")")"
DB_URL="postgresql://127.0.0.1:5432/${DB}"

# 静默收养护栏（#1349）：clean-worktree.sh 在「git worktree remove」与
# 「drop 派生库」之间被 SIGKILL（或 worktree 被手工移除而未清理派生库）时，
# 旧实例的派生库会留存；同名 worktree 重建后本脚本的 createdb 命中
# 「已存在可忽略」，旧实例数据被新实例静默沿用（#1084 目标缺陷形态的
# 残余窗口）。在改写 .env 之前、派生名锁内判定：
#   - 有初始化完成标记 → 幂等重跑，库就是本实例的，不检查；
#   - 无标记但 .env 已指向自身派生库 → 标记机制引入前的存量实例重跑，库
#     是本实例的，提示后沿用。归属判定只用严格锚定形态（行首锚定 + 可选
#     export 前缀 + 行尾锚定），故意不复用删除护栏
#     worktree_registration_owns_resources 的宽松子串兜底：子串匹配为
#     删除语境的 fail-closed 设计，在 init 语境方向反转成 fail-open——
#     基准派生库名是自身超串时（wt-7/wt-73、victim/victim2），本趟从基准
#     复制的 .env 会被误判为「指向自身」，静默收养原样发生（复验 P1-1）。
#     init 自身的 replace/append 必产出严格形态，真实存量重跑无损；手改
#     形态（引号包裹/行内注释）在 init 语境判「未指向」（与删除语境相反）；
#   - 库不存在 → 正常新建；
#   - 库存在且以上皆不命中 → 旧实例残留：fail-fast 并给出收口路径。拒绝
#     时本趟从基准复制的 .env 一并移除——它指向基准实例的库/bucket，留着
#     会被无视报错的启动误用，且基准库名是自身超串时还会让删除护栏的
#     宽松兜底把本实例误判为资源主、堵死收口路径（复验 P2-1）；
#     pre-existing .env 不动。无标记且移除后无 .env——删除护栏视本实例为
#     待初始化，不拦 drop-worktree-db.sh 的清理。
# psql 不可用或探测失败（Postgres 不可达）时降级为提示、保持原有宽容行为
# （离线场景不阻断初始化；此刻 createdb 同样不可用，不产生任何库动作）。
if [[ ! -f "$ROOT/$WORKTREE_INITIALIZED_MARKER" ]]; then
    if ! command -v psql >/dev/null 2>&1; then
        echo "提示: 未找到 psql，跳过派生库静默收养检查（#1349）。" >&2
    else
        stale_dbs=""
        probe_failed=""
        for probe_db in "$DB" "$(worktree_derived_test_db "$(basename "$ROOT")")"; do
            if ! probe_out="$(psql -d postgres -tAc \
                "select 1 from pg_database where datname='${probe_db}'" 2>/dev/null)"; then
                probe_failed=1
                break
            fi
            if [[ "$probe_out" == "1" ]]; then
                stale_dbs="${stale_dbs} ${probe_db}"
            fi
        done
        if [[ -n "$probe_failed" ]]; then
            echo "提示: 派生库存在性探测失败（Postgres 不可达？），跳过静默收养检查（#1349）。" >&2
            echo "      若本 worktree 是同名重建，请在 Postgres 恢复后确认上一实例的派生库已清理。" >&2
        elif [[ -n "${stale_dbs# }" ]]; then
            # 严格锚定归属判定（超串安全）：派生库名只含 [a-zA-Z0-9_]，正则安全。
            env_points_to_own_db=""
            if grep -qE "^(export )?AGENT_LEGION_DATABASE_URL=postgresql://127\.0\.0\.1:5432/${DB}\$" .env 2>/dev/null; then
                env_points_to_own_db=1
            fi
            if [[ -n "$env_points_to_own_db" ]]; then
                echo "派生库已存在且本实例 .env 已指向它（存量实例幂等重跑），沿用:${stale_dbs}"
            elif [[ "${AGENT_LEGION_INIT_ADOPT_EXISTING_DB:-}" == "1" ]]; then
                echo "警告: 按 AGENT_LEGION_INIT_ADOPT_EXISTING_DB=1 沿用已存在的派生库:${stale_dbs}（#1349）。" >&2
                echo "      请确认这些确为要继承的库；若是上一实例的清理残留，应立即中断并清理。" >&2
            else
                echo "错误: 派生库已存在，但本 worktree 未完成初始化（无 $WORKTREE_INITIALIZED_MARKER" >&2
                echo "      且 .env 未指向它）——这是上一同名实例清理中断（clean 在移除 worktree 后" >&2
                echo "      被 SIGKILL）或手工移除残留的旧库:${stale_dbs}" >&2
                echo "      静默复用会把旧实例数据带进新实例（#1349），已中止。" >&2
                if [[ -n "$ENV_COPIED_THIS_RUN" ]]; then
                    rm -f .env
                    echo "      本趟从基准复制的 .env 已移除（它指向基准实例的库/bucket）。" >&2
                fi
                echo "      复核后二选一：" >&2
                echo "        - 旧数据应删除: scripts/drop-worktree-db.sh $(basename "$ROOT") --yes 后重跑本脚本" >&2
                echo "        - 确认沿用旧库: AGENT_LEGION_INIT_ADOPT_EXISTING_DB=1 重跑本脚本" >&2
                exit 1
            fi
        fi
    fi
fi
if [[ -f .env ]]; then
    if grep -qE '^(export )?AGENT_LEGION_DATABASE_URL=' .env; then
        replace_in_place "s|^(export )?AGENT_LEGION_DATABASE_URL=.*|\1AGENT_LEGION_DATABASE_URL=${DB_URL}|" .env
    else
        echo "AGENT_LEGION_DATABASE_URL=${DB_URL}" >> .env
    fi
    echo "AGENT_LEGION_DATABASE_URL -> ${DB}"
fi
if command -v createdb >/dev/null 2>&1; then
    createdb "$DB" 2>/dev/null && echo "已创建数据库 ${DB}" || echo "数据库 ${DB} 已存在或建库失败（如已存在可忽略）"
    # role 隔离护栏（scripts/drop-worktree-db.sh）：派生库属主对齐
    # agent_legion_dev（集群存在该 role 时），清理路径走非 superuser
    # role，对共享/prod 库物理不可 drop；best-effort，失败仅 warning。
    if command -v psql >/dev/null 2>&1 \
        && psql -d postgres -tAc "select 1 from pg_roles where rolname='agent_legion_dev'" 2>/dev/null | grep -q 1; then
        psql -d postgres -qc "ALTER DATABASE \"$DB\" OWNER TO agent_legion_dev" 2>/dev/null \
            && echo "数据库 ${DB} 属主已对齐 agent_legion_dev" \
            || echo "提示: ${DB} 属主对齐 agent_legion_dev 失败（可手动 ALTER DATABASE OWNER）" >&2
    fi
else
    echo "提示: 未找到 createdb，请手动创建数据库 ${DB}" >&2
fi
else
    echo "tower 布局：复用父 worktree 的 AGENT_LEGION_DATABASE_URL（不创建独立库）"
fi

# 2.2 预暖 per-worktree .uv-cache：下方第一次 `uv run`（建 bucket / 生成
#     vault key）在空 cache 上要从零拉整棵依赖树（分钟级，慢网更甚）。克隆
#     基准 worktree 的 uv cache（内容寻址、append-only、路径无关）让后续 uv
#     调用命中已缓存依赖。协议实现在 scripts/uv_cache_prewarm.py（纯
#     stdlib：调用级独立中转目录（pid+nonce 命名）+ 入口按 pid 段判活
#     清扫残留 + lstat 安全
#     清理 + clonefile/reflink 克隆 + os.rename 原子 no-replace 落位），
#     deliberately 用系统 python3 而非 uv 调用——uv run 会先同步项目环境
#     并就地创建非空 cache，预暖永不生效（冷启动悖论，实测见脚本
#     docstring）。基准无 cache、目标已有 cache（幂等重跑）静默跳过；任何
#     失败只提示不 fail-init（冷启动仍是合法路径）。
if [[ -n "$BASE" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        python3 scripts/uv_cache_prewarm.py "$BASE" || true
    else
        echo "提示: 未找到 python3，跳过 .uv-cache 预暖（首次 uv 调用将冷启动拉取依赖）" >&2
    fi
fi

# 2.5 普通 worktree 建专属 S3 bucket；tower 子实例复用父 bucket。
if [[ -z "$TOWER_PARENT" ]]; then
# 材料存储（materials-and-runs 设计 §6.3）：开发机共享一个 RustFS 实例，
# bucket 按 worktree 名派生并无条件改写 .env；endpoint 与凭据随 .env 整体
# 从基准 worktree 继承。endpoint 不可达时跳过建 bucket / 配 CORS（warning，
# 不 fail——离线/CI 场景），此后材料 API 仅降级为 503。
BUCKET="$(worktree_derived_bucket "$(basename "$ROOT")")"
# 无条件改写为派生值（与上方 DATABASE_URL 块同一模式）：.env 是从基准
# worktree 复制的，本就带着基准的 bucket，「保留原值」会让所有派生
# worktree 共享基准 bucket，违背 per-worktree 隔离。
if grep -qE '^(export )?AGENT_LEGION_S3_BUCKET=' .env; then
    replace_in_place "s|^(export )?AGENT_LEGION_S3_BUCKET=.*|\1AGENT_LEGION_S3_BUCKET=${BUCKET}|" .env
else
    echo "AGENT_LEGION_S3_BUCKET=${BUCKET}" >> .env
fi
echo "AGENT_LEGION_S3_BUCKET -> ${BUCKET}"
# 建 bucket：逻辑抽在 scripts/ensure-s3-bucket.py（与 dev_stack.sh 共用），
# 复用 server/app/storage 的 env 加载（.env 经 load_dotenv 生效，
# override=False——调用 shell 已导出的同名变量优先）。任何失败（endpoint
# 不可达、boto3 缺失、凭据错误）都降级为提示，不阻断初始化。
# --frozen：依赖从冻结 lock 解析安装（fresh worktree 无 .venv 时也按 lock
# 建环境），绝不写 lock——否则开发者 shell 会话带 UV_DEFAULT_INDEX/UV_INDEX_URL
# 镜像变量时会触发 re-lock 把镜像 URL 写进 uv.lock（issue #526）。
if PYTHONPATH="$ROOT" UV_CACHE_DIR=.uv-cache uv run --frozen python scripts/ensure-s3-bucket.py .env; then
    :
else
    echo "提示: S3 endpoint 不可达或未配置，跳过建 bucket（材料 API 将降级为 503；" >&2
    echo "      启动共享 RustFS 后重跑本脚本即可补齐）。" >&2
fi
# 初始化完成标记（#1084 R5）：在锁内、派生资源建完后写入——clean/drop 的
# INCLUDE_SELF 删除护栏只认带标记（或存量实例的 .env 已指向派生库）的同名
# 登记为资源主；无标记的同名登记是阻塞在锁上的待初始化实例，不构成资源主。
# 标记已加入 .gitignore，不阻塞 git worktree remove，随 worktree 目录消失。
printf 'initialized_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$ROOT/$WORKTREE_INITIALIZED_MARKER"
else
    echo "tower 布局：复用父 worktree 的 AGENT_LEGION_S3_BUCKET（不创建独立 bucket）"
fi
# 撞名检查与资源动作已完成，其余步骤与派生名无关。
worktree_name_lock_release

# 3. deploy/secrets
mkdir -p deploy/secrets
if [[ ! -s deploy/secrets/vault_master_key ]]; then
    if [[ -n "$TOWER_PARENT" ]]; then
        cp "$BASE/deploy/secrets/vault_master_key" deploy/secrets/vault_master_key
        echo "已复制 deploy/secrets/vault_master_key <- $BASE"
    else
        # --frozen 同上（issue #526）：frozen 调用从不写 lock。
        UV_CACHE_DIR=.uv-cache uv run --frozen python -c \
            "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
            > deploy/secrets/vault_master_key
        echo "已生成 deploy/secrets/vault_master_key"
    fi
fi
chmod 600 deploy/secrets/vault_master_key

# 4. worker 状态副本 data/agent-worker-service/worker.yaml：worker 唯一生效
#    配置（issue #323，dev 侧不再有 config/agent-worker.yaml 种子）。缺失时
#    从基准 worktree 的状态副本复制并改写本实例 worker_id/name；普通实例按
#    角色设置 host_url，tower 子实例保留父实例的实际 host_url（#1409）。
#    后续修改走 worker 控制台或 PUT /api/config。
STATE_COPY=data/agent-worker-service/worker.yaml
if [[ ! -f "$STATE_COPY" ]]; then
    if [[ -n "$BASE" && -f "$BASE/$STATE_COPY" ]]; then
        # tower 子实例复用父后端的实际地址；普通实例按 worktree 角色分流端口
        # （prod=NATIVE_BACKEND_PORT，其余=DEV_BACKEND_PORT，#625）。
        if [[ -n "$TOWER_PARENT" ]]; then
            SEEDED_HOST_URL="$(sed -n 's/^host_url:[[:space:]]*//p' "$BASE/$STATE_COPY" | tail -n 1)"
        elif [[ "$(basename "$ROOT")" == "prod" ]]; then
            SEEDED_HOST_URL="http://127.0.0.1:${NATIVE_BACKEND_PORT:-8000}"
        else
            SEEDED_HOST_URL="http://127.0.0.1:${DEV_BACKEND_PORT:-8001}"
        fi
        mkdir -p data/agent-worker-service
        # register_token_file 是指向基准 worktree 绝对路径的实例私有字段，
        # 不能复制；scoped token 经 worker 控制台添加（issue #35）。
        grep -v '^register_token_file:' "$BASE/$STATE_COPY" > "$STATE_COPY"
        chmod 600 "$STATE_COPY"
        replace_in_place "s|^host_url:.*|host_url: ${SEEDED_HOST_URL}|" "$STATE_COPY"
        replace_in_place "s|^worker_id:.*|worker_id: ${NAME}|" "$STATE_COPY"
        replace_in_place "s|^name:.*|name: ${NAME} (worktree)|" "$STATE_COPY"
        echo "已生成 $STATE_COPY <- ${BASE}（host_url=${SEEDED_HOST_URL}、worker_id/name 已改写）"
    else
        echo "提示: 基准 worktree 无 worker 状态副本，跳过 worker 配置种子（首启后经 worker 控制台配置）" >&2
    fi
fi

cat <<EOF
完成。剩余手工步骤（如未做过）：
  - frontend: cd frontend && npm ci
  - 质量门内环索引: GATE_TIER=aff-index ./scripts/check-quick-backend.sh
    （一次性 ~2.5 分钟，建 .pytest-aff-index.json；此后改动后用
    GATE_TIER=aff ./scripts/check-quick.sh 快速内环，依赖/conftest 变更后重建）
  - 质量门: ./scripts/check-quick.sh
  - 材料存储: 若上方提示跳过了建 bucket，先启动共享 RustFS
    （deploy/compose.host.yaml 的 rustfs 服务）再重跑本脚本；未配置 S3 时
    材料 API 降级为 503，其余功能不受影响
  - workspace 调度: 后端每次启动把全部 workspace 重置为暂停（刻意设计），
    首次启动建表后按需执行 ./scripts/resume-workspaces.sh（或控制台手动恢复）
  - worker: claim 默认关闭（刻意设计），启动后经 worker 控制台（默认 8789）
    或 PUT /api/config 打开 claim_enabled；models allowlist 等配置修改
    一律走控制台/API（生效配置即状态副本 data/agent-worker-service/worker.yaml）
EOF
