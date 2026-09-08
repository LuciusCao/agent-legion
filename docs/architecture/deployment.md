# 部署与配置

## Overview

Agent Legion 使用 PostgreSQL 作为唯一控制面数据库；开发机和生产环境使用同一数据库语义。

## Directory Structure

```
config/
└── architecture/             # 架构治理配置（不变量、豁免、体积预算）

# worker 配置模板 config/agent-worker.example.yaml 已随 #323 退役：worker
# 唯一生效配置是状态副本 data/agent-worker-service/worker.yaml（控制台/API
# 驱动）；docker/远程部署的可选 bootstrap 模板见 deploy/worker.*.example.yaml。

# 运行时 split 配置（app.yaml / workflow.yaml / agent_legion.yaml）已整体退役：
# 代码默认值 + env 覆盖 + DB 实例设置文档，文件存在即启动报错（带迁移指引）。
# skill 侧：skills.yaml / skills.lock 与全局 skill_sources 注册表均已退役
# （#322）——skill 是 ~/.agents/skills/<group>/<name> 下的本地 in-place git
# 仓库（唯一模式）；pinned ref 的 commit 锁存 DB global_settings
# （skill_lock），经 make skills-lock 遍历锁内条目重解析。

data/                       # 文件产物（gitignored）
├── videos/                 # 下载的视频与产物
├── jobs/                   # Workspace Job 产物
├── packages/               # ZIP 输出
└── logs/                   # 处理日志

scripts/
├── check-quick.sh          # 快速质量门
└── check.sh                # 完整质量门
```

## Data Flow

```
开发者启动后端（uvicorn 8001）+ 前端（vite 5174）
    → 前端通过 Vite proxy 访问后端 API
    → 后端通过 PostgreSQL 协调任务，并读写 data/ 目录产物
    → Job 运行产物存入 data/jobs/<workspace>/<shard>/<job_id>/（详见 ../data-layout.md）；
      权威副本在实例对象存储（`jobs/{workspace_id}/{job_id}/{name}` key + `job_artifacts`
      清单表），本地 job_dir 只是执行暂存与可淘汰缓存
```

产物对象存储依赖 `AGENT_LEGION_S3_*` env 配置（自建可用 RustFS），部署细节见
[../materials-storage-deployment.md](../materials-storage-deployment.md)。

> 生产环境使用 8000/5173；dev worktree 默认 8001/5174，避免与 prod 端口冲突。

生产构建时，前端 `npm run build` 输出到 `frontend/dist/`，由 FastAPI 静态文件中间件托管。

## Key Decisions

- 使用 `uv` 而非 `pip`/`poetry`，依赖锁定在 `uv.lock`。
- PostgreSQL 是唯一运行时数据库；`server/` 与 `scripts/` 已无任何 SQLite 使用（曾用 SQLite 记录上传状态的 `tools/content-uploader` 已随业务清理退役删除）。
- 质量门分三层：本地 pre-push 默认 smoke 级（`scripts/run-local-gate.sh`，由 `.githooks/pre-push` 调用）；本地完整门 `check.sh`（`AGENT_LEGION_GATE_LEVEL=full` 触发）；CI（`.github/workflows/quality-gate.yml`）分阶段调用 `scripts/check-quick-backend.sh` / `check-quick-frontend.sh`，不调用 `check.sh`。
- 多 worktree 开发时，每个 worktree 使用独立的后端端口和 `data/` 目录；`scripts/init-worktree.sh` 会按 worktree 名派生并创建专属 Postgres 库与 S3 bucket（`AGENT_LEGION_S3_BUCKET`）。

## Worker 容器特权边界

> Issue #274。worker 容器（`deploy/compose.worker.yaml`）的特权组合是一项**刻意的
> 单点依赖**，本节固化其现状、风险定性与收敛路径；任何一项的收敛都必须先过容器内
> 实测，不允许机械叠加。

### 当前特权组合及各项的必要性

| # | 特权项 | 出处 | 为什么现在必须 |
|---|--------|------|----------------|
| 1 | 容器以 root 运行 | `Dockerfile` worker 阶段无 `USER` 指令 | 数据卷（`/var/lib/agent-legion-worker` 等）默认属 root，非 root uid 需要先解决卷属主（见收敛路径第 2 级） |
| 2 | `cap_add: SYS_ADMIN` | `deploy/compose.worker.yaml` | bwrap 沙箱在容器内创建 mount namespace / 执行挂载操作需要该 capability |
| 3 | `security_opt: seccomp:unconfined` | `deploy/compose.worker.yaml` | Docker 默认 seccomp profile 拦截 `unshare`，而 bwrap 依赖它建立 namespace（CI run 30683781370 实测，详见 `velites-harness.md` §5） |
| 4 | 镜像内 bwrap setuid（`chmod u+s /usr/bin/bwrap`） | `Dockerfile` worker 阶段 | bwrap 需要 setuid 位**或**非特权 user namespace 二者之一；宿主发行版常经 AppArmor 限制非特权 userns（`bwrap: setting up uid map: Permission denied`），setuid 是当前唯一在所有目标环境可复现的方案 |

缺失任一项时沙箱 fail-closed（`EXEC-HARNESS-SANDBOX-001`）：worker 启动即 exit≠0，
agent 全部秒退——这是可用性层面的硬依赖，不是可选配置。

### 风险定性

沙箱逃逸 = 宿主 root 的**单点依赖**：bwrap 一旦被绕过（或节点声明
`sandbox_network: true` 时 wrap 模式的 `--unshare-net` 例外生效），code 子进程即
拥有近似宿主 root 的能力。worker 侧其余安全设计——密钥 stdin 传递不落盘
（`worker/code_runner.py`）、沙箱 env 白名单（`shared/code_sandbox.py`）、
结果 JSON 严格校验（`workspace_libs/code_child.py`）、preflight fail-closed
（`worker/runtime/preflight.py`）——的收益全部押在 bwrap 单点可靠性上。
因此 worker 容器应按**不可信执行边界**对待：不要把宿主敏感路径、Docker socket
或生产凭据挂给它。

### 分级收敛路径

每级独立可落地，落地顺序即风险收益排序；**任何一级都必须先在真实容器里实测
沙箱 e2e（含 bwrap 启动、node 执行、fail-closed 行为）再合入**：

1. **`no-new-privileges`（需实测验证，未落地）**：`security_opt` 追加
   `no-new-privileges:true` 可削弱 setuid 提权面，但它会阻止 setuid 提权——而
   bwrap 恰恰依赖 setuid 位（非 user namespace 场景），**机械叠加可能直接破坏
   沙箱**（表现为 bwrap 起 uid map 失败 → fail-closed → worker 全部节点秒退）。
   必须先在容器内实测两种形态：setuid bwrap + no-new-privileges 是否仍能建立
   namespace；若不能，评估改走非特权 userns 形态后再加该 flag。
2. **镜像非 root uid + chown 数据卷**：`Dockerfile` worker/host 阶段加专用
   uid + `chown` 数据卷目录，消除「容器内即 root」的兜底特权。需实测：
   数据卷首次挂载的属主、worker 控制文件的写权限、pi/velites 运行时目录。
3. **userns-remap / 非 root + unprivileged userns（评估）**：docker daemon 侧
   `userns-remap` 或较新 runc 对 `bwrap --unshare-user` 的支持，可让第 4 项
   setuid 依赖退役；涉及宿主 daemon 全局配置，收益与代价需单独评估。
4. **边界管理（持续）**：worker 容器按不可信边界对待——只读 rootfs、独立网络
   段、最小 volume 挂载面；即使特权收敛完成，这层也保持不变。

## 单副本约束

> Issue #277。控制平面（FastAPI Host 进程）当前是**单副本形态**：多个运行时设施
> 刻意放在进程内，数据库只是部分状态的持久层。误把 uvicorn/compose 的水平扩缩容
> 直觉搬过来（`--workers N`、多容器副本、K8s Deployment replicas>1），功能不会崩溃
> 但会**静默退化**——每个症状都长得像另一个 bug。本节固化这份现状与症状形态，
> 并说明已内置的第二副本探测护栏。#521 方案 B 的角色拆分（见下）是**刻意双进程
> 形态**：每个平面仍是单副本，只是把 API 面与调度面分进两个进程。

### 进程内的运行时状态

| # | 状态 | 位置 | 多副本下的症状形态 |
|---|------|------|--------------------|
| 1 | 事件总线（SSE fan-out） | `InProcessEventBus`（`server/app/events/bus.py`） | 副本 A 写入的 job/agent 事件只广播给连在 A 上的 SSE 客户端；连在 B 上的浏览器收不到该事件，表现为「任务明明在跑但界面不动」 |
| 2 | 登录限速 | `LoginRateLimiter`（`server/app/auth/rate_limit.py`） | 每副本各自计数，暴力破解配额被副本数稀释（N 副本 ≈ N×5 次失败窗口） |
| 3 | Studio Chat 会话 | `StudioChatService._runtimes`（`server/app/studio_chat/service.py`） | 会话的 agent 子进程只活在创建它的副本里；请求被负载均衡到另一副本时该会话互不可见，表现为「会话时有时无 / 无法继续」 |
| 4 | 暂停状态启动重置 | `WorkspaceWorkerControl.reset_all_to_paused`（`server/app/worker_control.py`，`main.py` 启动调用） | 副本 B 启动即把全部 workspace 重置为暂停，把副本 A 上刚由操作员恢复的调度一并打掉，两个副本的暂停语义互相打架 |

### 当前正确形态与护栏

- **当前部署形态（单 uvicorn 进程 × 每数据库一个副本）全部正确**：开发机
  `make dev`、生产 `scripts/native-prod-up.sh` / `deploy/` compose 均如此
  （prod 启动器自 #521 角色拆分起默认双平面，见下；导出
  `AGENT_LEGION_HOST_ROLE=combined` 后重跑 `native-prod-up.sh` 回退单进程
  形态，compose 侧则需自行裁剪 scheduler 服务）。多 worktree 开发也天然
  合规——`scripts/init-worktree.sh` 给每个 worktree 派生专属数据库，
  「两个进程、两个库」不触发本节任何症状。
- **第二副本探测（`server/app/single_replica_probe.py`）**：lifespan 启动时在一条
  专用池连接上取会话级 advisory lock（key 与 `current_database()` 一起哈希，跨库不
  互撞）。后启动的副本发现锁被占即打一条 warning 日志（SSE / 限速 / 会话 / 暂停
  各自退化的提示 + 逃生门指引），不拒绝启动——自托管单机下「两个 worktree 实例连
  不同库」是合法形态，fail-fast 会误伤；同库多副本才是危险形态，而探测的 key 恰好
  以库为粒度。连到 shutdown 才释放，连接归池不泄漏。
- 逃生门与开关：
  - `AGENT_LEGION_ALLOW_MULTI_REPLICA=1`：知情确认多副本，warning 降为 info；
  - `AGENT_LEGION_SKIP_SINGLE_REPLICA_PROBE=1`：完全跳过探测（测试/特殊场景）。
- **角色拆分（#521 方案 B，刻意双进程形态）**：`AGENT_LEGION_HOST_ROLE`
  把控制平面拆成两个单副本平面——`http`（uvicorn API 面：路由、result
  commit、claim、心跳、dashboard SSE、Studio chat）与 `scheduler`
  （`python -m server.app.scheduler_process`：sweeper、workflow worker、
  慢速清扫、指标采样）。默认 `combined` 保持单进程形态不变。拆分形态
  下两平面各持一把探针锁（`control-plane-http` / `scheduler`），互相
  不误报；**同一平面的第二个进程仍会被检出**（两个 uvicorn http 平面
  或两个 scheduler 进程都触发 #277 警告）。跨平面的归属划分：
  - 可调度工作唤醒：HTTP 平面的写路径（run 提交、发布、审批……）经
    PostgreSQL `NOTIFY agent_legion_schedulable` 桥（`scheduler_notify.py`）
    唤醒调度进程的 poll 循环；payload 标记触发类型（`schedulable` 空排 /
    `scan_reload` 扫描列表变更——http 平面的 workspace 创建/首次发布经
    此让调度进程先重载 scan list 再唤醒，否则新 workspace 要等调度进程
    重启才会被扫到）；best-effort，丢通知由 scheduler 3s 空转 poll 兜底
    （桥只买延迟，不买正确性，`scan_reload` 除外——丢了要重启收敛）。
    空领（empty claim）的补货信号同样走该桥（防抖留在 http 平面本地）。
  - 指标采样：只在 scheduler 进程跑（`ops_metric_samples` /
    `ops_runtime_profile_samples` 的分钟桶 upsert 是每进程覆盖写，双写
    丢 (N-1)/N 数据）；HTTP 平面的 `/api/metrics/*` 读路由查表不采表。
    已知观测取舍：claim/result 的**进程内计数器**落在各自进程——拆分
    形态下运行画像表的 claim/result 及分段列只反映 scheduler 进程本地
    流量，读作零不代表 http 平面无流量；#530 的 result 分段观测
    （`result stages:` 日志行）仍在 http 平面进程日志里逐请求可见，
    需要分钟级聚合画像时回 combined 形态。深度类指标（队列深度、
    active、token 用量）与 pass 类指标来自 DB/调度进程，不受影响。
  - `reset_all_to_paused`：只在 combined/scheduler 角色启动时执行
    （scheduler 进程入口同样执行——拆分形态下两平面只有它做重置）；
    HTTP 平面滚动重启不再抹掉运行中部署的操作员恢复状态。
  - intake 异步消费：留在 HTTP 平面（BackgroundTasks 随 lifespan 无条件
    启动）；`claim_intake_run` 的 DB claim 语义本就多消费者安全，调度
    进程不重复消费即可。消费后经 NOTIFY 桥唤醒调度进程。
  - 已知取舍：dashboard SSE 事件、Studio chat 会话、登录限速仍在 HTTP
    平面进程内（#277 表格的 1/2/3 项语义不变）；调度进程的健康面是其
    日志（`data/logs/prod-scheduler.log`，compose 侧 `restart:
    unless-stopped` 托管存活）。
- 若未来确实需要多副本，正确路径不是简单横向扩缩容，而是把上表逐项外置
  （事件总线走 pub/sub、限速与暂停状态本就以 DB 为权威、Chat 会话需要粘性路由或
  会话外置），每项都是独立的设计工作，不在本节展开。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### 顶层配置项

_全部运行时配置段已从 split yaml 退役：业务参数在 capability config_schema（Studio 节点/workspace 配置覆盖），实例级调参在 DB 实例设置文档（/api/admin/instance-settings），机器路径与密钥走 env（如 AGENT_LEGION_DATABASE_URL）。_

<!-- END AUTO-GENERATED -->
