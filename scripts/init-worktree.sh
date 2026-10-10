#!/usr/bin/env bash
# 初始化新 git worktree 的开发环境（幂等，可重复执行）：
#   0. 嵌套防护：worktree 必须是主仓库根的平级子目录，嵌套直接报错
#   1. 从基准 worktree 复制 .env（若本 worktree 缺失；无法复制则 fail-fast——
#      缺 .env 会让后端回落共享默认库/prod）
#   2. 把 AGENT_LEGION_DATABASE_URL 指向按 worktree 名派生的专属 Postgres 库并尝试建库
#   2.2 预暖 .uv-cache：从基准 worktree 克隆 uv 缓存（clonefile/reflink，
#       失败仅提示并回退冷启动），让首次 uv run 免于从零拉依赖树
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

# 0. 嵌套防护（AGENTS.md §1）：worktree 一律是主仓库根的平级子目录，
#    嵌套会让 data/、测试库派生与清理路径全部混乱，直接拒绝。
MAIN="$(git worktree list --porcelain | awk '/^worktree /&&!seen{print $2; seen=1}')"
if [[ "$ROOT" != "$MAIN" && "$(dirname "$ROOT")" != "$MAIN/.worktrees" ]]; then
    echo "错误: worktree 禁止嵌套（当前: ${ROOT}）。" >&2
    echo "请先 cd 到主仓库根（${MAIN}），再 git worktree add .worktrees/<name> -b <branch> <base>。" >&2
    exit 1
fi

# 在主仓库根本身执行时直接退出（bare 主仓库无工作区，更不会走到这里）
if [[ "$ROOT" == "$MAIN" ]]; then
    echo "当前就是主仓库根，无需初始化。" >&2
    exit 0
fi

# 0.5 派生名撞名防护（#950）：worktree 名到库/bucket 名的映射不是单射，
#     与其他已注册 worktree 派生出同名资源时 fail-fast（在任何副作用之前），
#     否则两个 worktree 会静默共用同一个库与 bucket。
worktree_require_unique_derived_names "$ROOT" "$(basename "$ROOT")" "初始化（建库/建 bucket）" || exit 1

BASE="${1:-}"
if [[ -z "$BASE" ]]; then
    # 默认取第一个非 bare、非当前、非主仓库根的 worktree 作基准（主仓库根是
    # bare/无工作区配置，永不适合作基准）。注意选中的基准自身也可能缺 .env，
    # 由下方 fail-fast 兜底（2026-08-18 事故：基准 worktree 缺 .env → 新
    # worktree 无 .env → 后端回落共享默认库即 prod 库）。
    BASE="$(git worktree list --porcelain | awk -v root="$ROOT" -v main="$MAIN" '
        /^worktree / {
            if (wt != "" && !isbare && wt != root && wt != main) { print wt; found=1; exit }
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

# 1. .env
if [[ ! -f .env ]]; then
    if [[ -z "$BASE" ]]; then
        echo "警告: 未找到基准 worktree（主仓库为 bare 且无其他 worktree），跳过 .env 复制" >&2
    elif [[ -f "$BASE/.env" ]]; then
        cp "$BASE/.env" .env
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

# 2. 专属 Postgres 库
DB="$(worktree_derived_db "$(basename "$ROOT")")"
NAME="${DB#agent_legion_}"
DB_URL="postgresql://127.0.0.1:5432/${DB}"
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

# 2.2 预暖 per-worktree .uv-cache：下方第一次 `uv run`（建 bucket / 生成
#     vault key）在空 cache 上要从零拉整棵依赖树（分钟级，慢网更甚）。uv
#     cache 内容寻址、append-only、路径无关，克隆基准 worktree 的即可让
#     后续 uv 调用命中已缓存依赖：APFS 走 clonefile、Linux 走
#     --reflink=auto（写时复制，秒级零额外磁盘；不支持的卷上 cp 内部各自
#     回退普通复制，仍是磁盘速度、远快于网络）。基准无 cache、目标已有
#     cache（幂等重跑）静默跳过；克隆/落位失败只提示不 fail-init（冷启动
#     仍是合法路径），半成品临时目录必须清掉，避免坏条目污染新 cache。
if [[ ! -d .uv-cache && -n "$BASE" && -d "$BASE/.uv-cache" ]]; then
    # symlink 基准解引用：cp 一个 symlink 会在新 worktree 复制出指向共享
    # 目标的 symlink——「独立」cache 实为共享，目标随被清理的 worktree
    # 消失时还留悬空链接。解引用后克隆实体目录（内容寻址、路径无关，
    # 克隆本身无害），保住 per-worktree 隔离且不浪费现成的缓存；解引用
    # 失败则跳过预暖（warn，不 fail-init）。
    CACHE_SRC="$BASE/.uv-cache"
    if [[ -L "$CACHE_SRC" ]]; then
        CACHE_SRC="$(cd "$CACHE_SRC" 2>/dev/null && pwd -P || true)"
    fi
    if [[ -z "$CACHE_SRC" ]]; then
        echo "提示: 基准 .uv-cache 为 symlink 且解引用失败，跳过预暖（首次 uv 调用将冷启动拉取依赖）" >&2
    else
        if [[ "$(uname)" == "Darwin" ]]; then
            CLONE_FLAGS=(-Rc)
        else
            CLONE_FLAGS=(-R --reflink=auto)
        fi
        # 落位段互斥（PR #1182 codex P2）：重判与 mv 之间仍有窗口——两个
        # init 都观察到 .uv-cache 不存在后先后 mv，BSD mv 对「目标是已存在
        # 目录」不报错，而把后到者的临时目录挪进
        # .uv-cache/.uv-cache.prewarm.<pid>，留下隐藏的完整重复缓存。
        # mkdir 原子（目标已存在即失败）作互斥锁，只有持锁进程执行
        # 「重判 + mv + 释放锁」，未持锁者丢弃克隆走跳过路径。锁目录在
        # repo 根（不进 .uv-cache，不撞 uv bucket 命名），名字匹配
        # .gitignore 的 .uv-cache.prewarm.*。残锁处理：锁内记录 holder
        # pid，竞争者仅在 pid 文件非空且 kill -0 判死时才回收重试一次
        # （pid 缺失/为空/读不出一律视为存活走丢弃——安全方向），避免
        # 持锁进程被 SIGKILL 后预暖被残锁永久静默跳过。
        TMP_CACHE=".uv-cache.prewarm.$$"
        LOCK_DIR=".uv-cache.prewarm.lock"
        # SIGKILL 兜底清扫：EXIT trap 覆盖不到 SIGKILL，被杀进程的临时目录
        # 会残留（.gitignore 隐藏、可能数 GB 死重）。只清「名字后缀为纯数字
        # pid 且该 pid 已死」的目录——活进程（含并发兄弟）与非数字后缀
        # （锁目录 .uv-cache.prewarm.lock）一律不动；pid 复用只会让清扫
        # 保守跳过（安全方向：最坏是保留死重，绝不误删活跃数据）。
        for STALE in .uv-cache.prewarm.*; do
            [[ -d "$STALE" ]] || continue
            STALE_PID="${STALE##*.uv-cache.prewarm.}"
            case "$STALE_PID" in
                *[!0-9]* | "") continue ;;
            esac
            if ! kill -0 "$STALE_PID" 2>/dev/null; then
                rm -rf "$STALE" || true
            fi
        done
        # 区段级 EXIT trap（codex P2 第二轮）：克隆进行中或持锁落位前收到
        # Ctrl-C/SIGTERM 时 bash 退出前会执行 EXIT trap（实证：非交互 bash
        # 等待前台子进程时被 SIGTERM 仍跑 trap）——清理本进程临时目录
        # （pid 标记，总是可清）与锁（仅 LOCK_HELD 置位即本进程持有时才
        # 可清，误删他进程持有的锁会破坏互斥）。区段正常走完即 trap - EXIT
        # 解除；本脚本无其他 EXIT trap（已 grep 确认），无需保存/恢复。
        LOCK_HELD=""
        prewarm_release_lock() {
            if [[ -n "$LOCK_HELD" ]]; then
                rm -rf "$LOCK_DIR" || true
                LOCK_HELD=""
            fi
        }
        prewarm_cleanup() {
            rm -rf "$TMP_CACHE" || true
            prewarm_release_lock
        }
        trap 'prewarm_cleanup' EXIT
        if cp "${CLONE_FLAGS[@]}" "$CACHE_SRC" "$TMP_CACHE"; then
            if mkdir "$LOCK_DIR" 2>/dev/null; then
                echo $$ > "$LOCK_DIR/pid" || true
                LOCK_HELD=1
            elif [[ -s "$LOCK_DIR/pid" ]] && ! kill -0 "$(cat "$LOCK_DIR/pid")" 2>/dev/null; then
                rm -rf "$LOCK_DIR" || true
                if mkdir "$LOCK_DIR" 2>/dev/null; then
                    echo $$ > "$LOCK_DIR/pid" || true
                    LOCK_HELD=1
                fi
            fi
            if [[ -z "$LOCK_HELD" ]]; then
                prewarm_cleanup
                echo "提示: .uv-cache 正由并发 init 预暖，丢弃重复克隆" >&2
            elif [[ -d .uv-cache ]]; then
                prewarm_cleanup
                echo "提示: .uv-cache 已由并发 init 预暖，丢弃重复克隆" >&2
            elif mv "$TMP_CACHE" .uv-cache; then
                if [[ -e ".uv-cache/$TMP_CACHE" ]]; then
                    # 锁外 actor（同 worktree 的并发 uv 调用不拿本锁）在
                    # 重判与 mv 之间创建了 .uv-cache：BSD mv 已把临时目录
                    # 挪进去——回收嵌套克隆，对方 cache 原样保留，走跳过。
                    rm -rf ".uv-cache/$TMP_CACHE" || true
                    prewarm_release_lock
                    echo "提示: .uv-cache 在预暖落位期间被并发创建，丢弃重复克隆" >&2
                else
                    prewarm_release_lock
                    echo "已预暖 .uv-cache <- ${BASE}（后续 uv 调用将命中已缓存依赖）"
                fi
            else
                prewarm_cleanup
                echo "提示: .uv-cache 预暖落位失败，已跳过——首次 uv 调用将冷启动拉取依赖（正常路径，仅较慢）" >&2
            fi
        else
            prewarm_cleanup
            echo "提示: .uv-cache 预暖克隆失败，已跳过——首次 uv 调用将冷启动拉取依赖（正常路径，仅较慢）" >&2
        fi
        # 区段正常走完：临时目录已落位/清理、锁已释放，解除 trap（此后
        # 触发也是无害 no-op，但本区段已无可清理状态）。
        trap - EXIT
    fi
fi

# 2.5 专属 S3 bucket（材料存储，materials-and-runs 设计 §6.3）：开发机共享一个
#     RustFS 实例，bucket 按 worktree 名派生并无条件改写 .env（与专属
#     Postgres 库同一模式）；endpoint 与凭据随 .env 整体从基准 worktree 继承。
#     endpoint 不可达时跳过建 bucket / 配 CORS（warning，不 fail——离线/CI
#     场景），此后材料 API 仅降级为 503。
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

# 3. deploy/secrets
mkdir -p deploy/secrets
if [[ ! -s deploy/secrets/vault_master_key ]]; then
    # --frozen 同上（issue #526）：frozen 调用从不写 lock。
    UV_CACHE_DIR=.uv-cache uv run --frozen python -c \
        "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
        > deploy/secrets/vault_master_key
    echo "已生成 deploy/secrets/vault_master_key"
fi
chmod 600 deploy/secrets/vault_master_key

# 4. worker 状态副本 data/agent-worker-service/worker.yaml：worker 唯一生效
#    配置（issue #323，dev 侧不再有 config/agent-worker.yaml 种子）。缺失时
#    从基准 worktree 的状态副本复制并改写本实例字段（host_url 按 worktree
#    角色分流端口：prod worktree 的后端在 NATIVE_BACKEND_PORT（默认 8000，
#    make prod-up），其余指向开发后端 DEV_BACKEND_PORT（默认 8001）——无
#    条件种子 dev 端口会让 prod worktree 的 worker 静默连错端口、退避重试
#    不易察觉（#625）；worker_id 按 worktree 派生）。后续修改走 worker
#    控制台或 PUT /api/config。
STATE_COPY=data/agent-worker-service/worker.yaml
if [[ ! -f "$STATE_COPY" ]]; then
    if [[ -n "$BASE" && -f "$BASE/$STATE_COPY" ]]; then
        # 与 native-prod-up.sh 的端口约定一致（NATIVE_BACKEND_PORT 默认 8000）；
        # 目录名按 basename 精确匹配 prod（AGENTS.md §1 的生产 worktree 惯例）。
        if [[ "$(basename "$ROOT")" == "prod" ]]; then
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
