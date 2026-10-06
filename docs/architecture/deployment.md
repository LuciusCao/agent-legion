# 部署约束与配置来源

本文只收部署形态的**硬约束**（Worker 容器特权边界、Host 单副本、浏览器安全头）与配置来源；
操作步骤在运维 runbook：Host / Worker 部署见
[../agent-worker-deployment.md](../agent-worker-deployment.md)，对象存储见
[../materials-storage-deployment.md](../materials-storage-deployment.md)，PostgreSQL 见
[../postgresql-runbook.md](../postgresql-runbook.md)。目录结构见
[project-structure.md](project-structure.md)，质量门见
[local-quality-gates.md](local-quality-gates.md)。

## 运行形态

| 形态 | 入口 | 端口（默认） |
|------|------|--------------|
| 开发 | `make dev-up`（`scripts/dev_stack.sh`） | 后端 :8001、Vite 控制台 :5174（代理 `/api`）、Worker 控制台 :8789 |
| 生产（原生） | `make prod-up`（`scripts/native-prod-up.sh`） | 后端 :8000（同时托管 `frontend/dist/` 构建产物）、Worker 控制台 :8787；`NATIVE_BACKEND_PORT` / `NATIVE_WORKER_PORT` 覆盖 |
| 生产（Docker） | `make prod-up docker`（`scripts/stack-prod-up.sh` → `deploy/compose.host.yaml`） | Host :8000（`AGENT_LEGION_HOST_BIND` 控制绑定地址） |

- PostgreSQL 是唯一控制面数据库，开发机与生产语义一致；`server/` 与 `scripts/` 不使用 SQLite。
- Job 产物权威副本在实例对象存储（`AGENT_LEGION_S3_*`，默认本地 SeaweedFS），本地 job_dir
  只是执行暂存与可淘汰缓存（EXEC-ARTIFACT-STORE-001；key 布局见
  [artifact-direct-url-pinning.md](artifact-direct-url-pinning.md)，`data/` 布局见
  [../data-layout.md](../data-layout.md)）。
- 配置来源：代码默认值 + env 覆盖（机器路径、密钥、DB URL 等 env-only 项）+ DB 实例设置文档；
  运行时 split yaml 已全部退役，详见 [backend.md「Configuration Reference」](backend.md#configuration-reference)。
  Worker 唯一生效配置是状态副本 `data/agent-worker-service/worker.yaml`（控制台 / API 驱动），
  docker / 远程部署的可选 bootstrap 模板见 `deploy/worker.*.example.yaml`。
- 多 worktree 开发时每个 worktree 用独立端口与 `data/`，`scripts/init-worktree.sh` 按 worktree
  名派生专属 Postgres 库与 S3 bucket。

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
> 并说明已内置的第二副本探测护栏。

### 进程内的运行时状态

| # | 状态 | 位置 | 多副本下的症状形态 |
|---|------|------|--------------------|
| 1 | 事件总线（SSE fan-out） | `InProcessEventBus`（`server/app/events/bus.py`） | 副本 A 写入的 job/agent 事件只广播给连在 A 上的 SSE 客户端；连在 B 上的浏览器收不到该事件，表现为「任务明明在跑但界面不动」 |
| 2 | 登录限速 | `LoginRateLimiter`（`server/app/auth/rate_limit.py`） | 每副本各自计数（账号 + IP / 账号两个维度，#970），暴力破解配额被副本数稀释（N 副本 ≈ 每把钥匙 N 倍失败窗口） |
| 3 | Studio Chat 会话 | `StudioChatService._runtimes`（`server/app/studio_chat/service.py`） | 会话的 agent 子进程只活在创建它的副本里；请求被负载均衡到另一副本时该会话互不可见，表现为「会话时有时无 / 无法继续」 |
| 4 | 暂停状态启动重置 | `WorkspaceWorkerControl.reset_all_to_paused`（`server/app/worker_control.py`，`main.py` 启动调用） | 副本 B 启动即把全部 workspace 重置为暂停，把副本 A 上刚由操作员恢复的调度一并打掉，两个副本的暂停语义互相打架 |
| 5 | hydration defer 公告（#887） | `HydrationDeferBoard`（`server/app/services/hydration_defer_board.py`），由 workflow worker 线程写入 | 只有恰好评估到该 job 的副本知道「输入恢复不全」；请求落到另一副本时 job 详情不显示提示，节点看起来只是普通「等待中」 |

### 当前正确形态与护栏

- **当前部署形态（单 uvicorn 进程 × 每数据库一个副本）全部正确**：开发机
  `make dev-up`、生产 `scripts/native-prod-up.sh` / `deploy/` compose 均如此。多 worktree
  开发也天然合规——`scripts/init-worktree.sh` 给每个 worktree 派生专属数据库，
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
- 若未来确实需要多副本，正确路径不是简单横向扩缩容，而是把上表逐项外置
  （事件总线走 pub/sub、限速与暂停状态本就以 DB 为权威、Chat 会话需要粘性路由或
  会话外置），每项都是独立的设计工作，不在本节展开。

## 浏览器安全头与低权读面

> Issue #752（#710 红队审计 P3 收口）。

- **文档 CSP**：Host 对所有 `text/html` 响应（SPA 外壳与 catch-all）附加
  `Content-Security-Policy`（`server/app/http_csp.py`），作为 DOMPurify 之后的第二层。
  `script-src` 暂保留 `'unsafe-inline'`：`srcdoc` iframe 继承宿主文档的策略，预览面板
  bundle 按契约是 inline 脚本，收紧会让面板整体失效；其余指令（`connect-src` 限同源 +
  对象存储 presign 源、`frame-ancestors 'self'`、`object-src 'none'`、`base-uri`、
  `form-action`）照常生效。改前端外链资源（字体、图源、上传直连）时同步改该模块。
- **`GET /api/agent-workers`**：admin 全量；其他身份只见准入范围与自身可见 workspace
  有交集的 Worker，`allowed_workspaces` 裁剪为交集、`register_token_ids` 置空，可见性
  规则与 workspace 列表同源（`server/app/auth/workspace_visibility.py`）。
- **`GET /api/connections/keys`**：有意对全部登录用户开放（#419，ref 条目与节点配置要选
  key）。连接是实例级、无 workspace 归属，只回 key 名、不回配置；若将来引入按 workspace
  归属的连接再改为按成员过滤。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### 顶层配置项

_全部运行时配置段已从 split yaml 退役：业务参数在 capability config_schema（Studio 节点/workspace 配置覆盖），实例级调参在 DB 实例设置文档（/api/admin/instance-settings），机器路径与密钥走 env（如 AGENT_LEGION_DATABASE_URL）。_

<!-- END AUTO-GENERATED -->
