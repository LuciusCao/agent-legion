# Agent Legion Host 与 Worker 部署

Agent Legion 把服务拆成两个角色：Host 负责工作流、数据库与任务调度；Worker Service 负责本机配置、状态查询和运行 Agent。即使同一台部署机同时承担两个角色，也运行两个独立服务。

Worker Service 宿主机发布面默认只绑定 `127.0.0.1:8787`（控制台和查询 API）；compose 网络内其它容器可以到达该端口，但除 `GET /api/health` 外所有端点都要求本地 control token 鉴权。首次启动会把现有只读 YAML 导入独立的可写控制卷；此后可以直接在页面中修改配置，不再需要编辑 YAML。注册密钥既可以通过 Docker secret 提供，也可以在本机控制台中填写；页面和 API 只接受写入、不回显明文，托管副本以 `0600` 权限保存在控制卷中。

LLM gateway 是独立基础设施，不属于 Agent Worker 协议。Worker 容器通过挂载自己的 Pi 配置访问它。

## 1. 部署机准备密钥

在仓库根目录执行：

```bash
mkdir -p deploy/secrets
openssl rand -hex 32 > deploy/secrets/postgres_password
openssl rand -hex 32 > deploy/secrets/agent_worker_register_token
```

把 PostgreSQL 密码写入 pgpass。以下命令中的 `<postgres-password>` 必须替换成 `deploy/secrets/postgres_password` 文件里的值：

```text
postgres:5432:agent_legion:agent_legion:<postgres-password>
```

将这一行保存为 `deploy/secrets/postgres_pgpass`，然后限制密钥文件权限：

```bash
chmod 600 deploy/secrets/postgres_password deploy/secrets/postgres_pgpass deploy/secrets/agent_worker_register_token
```

## 2. 部署机准备挂载目录

设置 Agent skills 和 Pi 配置目录。Pi 配置中继续使用已验证可用的 provider/model 与 LLM gateway 地址。

```bash
export AGENT_SKILLS_DIR="$PWD/skills"
export PI_CONFIG_DIR="$HOME/.pi/agent"
```

如果 Worker 机器要通过 Tailscale 等 overlay 网络访问 Host，将监听地址设为部署机的 overlay 网络 IP：

```bash
export AGENT_LEGION_HOST_BIND=192.0.2.1
```

如果 LLM gateway 绑定了 Tailnet 地址并设置了 `LLM_GATEWAY_TOKEN`（绑定非 loopback 地址时必须设置），Worker 容器也需要同一个 token 才能调用 gateway。Compose 通过环境变量透传它，把 token 写进 `deploy/.env`（该文件已被 `.gitignore` 与 `.dockerignore` 排除）或导出到 shell：

```bash
echo 'LLM_GATEWAY_TOKEN=<gateway-token>' >> deploy/.env
chmod 600 deploy/.env
```

不要把它写进 Compose YAML、worker.yaml 或命令行。Worker supervisor 会把它透传给 Pi 子进程；上游 LLM provider 凭证本身只存在于 gateway 进程，不经过 Worker。

## 3. 启动部署机的 stack

```bash
make stack-host-up
curl http://192.0.2.1:8000/api/health
```

该命令启动 PostgreSQL、Host 和部署机本地 Worker。它们使用 [compose.host.yaml](../deploy/compose.host.yaml) 编排。

## 4. Worker 机器准备

将同一版本的仓库放到 Worker 机器，并准备注册密钥目录：

```bash
mkdir -p deploy/secrets
```

无需先复制或编辑 Worker YAML：首次启动会导入仓库内的引导配置，随后在本机控制台填写 Host 地址、Worker ID 和能力。已有引导 YAML（如复制自 `deploy/worker.home.example.yaml`）的机器可继续使用；启动前设置 `AGENT_WORKER_CONFIG=./<your-worker>.yaml`，Worker Service 会在首次启动时导入它。

Worker 的注册 token 决定它能进入哪些 workspace——**token 即 scope**，`worker.yaml` 不需要也不允许声明 workspace。两种 token：

- **全局 token**：把部署机的 `deploy/secrets/agent_worker_register_token` 安全复制到 Worker 机器的同一路径；不要把它提交到 Git。Worker 注册后可承接全部 workspace 的任务。
- **Scoped token（需要把 Worker 隔离到单个 workspace 时使用）**：推荐在 Host Web UI 的「设置 → Worker Token」页面签发与管理：填写标签与可选的 workspace 范围即可创建，明文只显示一次，复制后保存为 Worker 机器上的 `deploy/secrets/agent_worker_register_token`（权限 600）。该页面同时支持查看/吊销已签发 token 与吊销已注册 Worker。

  该 Worker 注册后只能看到并 claim 对应 workspace 的任务。

  注意：这些管理端点（UI 与下列 curl 共用的 `/api/agent-register-tokens*`、`/api/agent-workers/*/revoke`）要求 **admin 会话**调用，必须携带登录 cookie 与 CSRF header（见 Host Web UI 登录后的会话）。Worker 注册本身仍必须凭 register token（全局或 scoped），不受影响。

  也可以用 curl 在部署机上签发（备选方式）。这些请求需要 admin 会话：先在 Host Web UI 登录（或调用 `/api/auth/login`）取得登录 cookie，并在请求中带上 CSRF header，否则返回 401/403。

```bash
curl -sS -X POST http://192.0.2.1:8000/api/agent-register-tokens \
  -H 'Content-Type: application/json' \
  -d '{"workspace_id": "<workspace_id>", "label": "remote-worker"}'
# => {"token_id": "...", "register_token": "<明文，只返回这一次>", "workspace_id": "<workspace_id>", "label": "remote-worker"}
```

```bash
# 列表（不含明文与 hash，含吊销状态）
curl -sS http://192.0.2.1:8000/api/agent-register-tokens

# 吊销
curl -sS -X POST http://192.0.2.1:8000/api/agent-register-tokens/<token_id>/revoke
```

注意：吊销 scoped token 只影响后续注册；已注册 Worker 落库的 scope 在重新注册前不变。需要立即收缩时，吊销后让该 Worker 重新注册（换用新 token）。

Worker 机器上继续挂载它自己的 Pi 配置，并在 gateway 设置了 token 时同样提供 `LLM_GATEWAY_TOKEN`（见 §2）：

```bash
export PI_CONFIG_DIR="$HOME/.pi/agent"
```

容器内运行的是 Linux，因此 Worker 标签中的 `os: linux` 是有意的；`arch: arm64` 对应 Apple Silicon 容器架构。

## 5. 启动 Worker 机器上的 Worker

```bash
make stack-worker-up
make stack-logs STACK=worker
```

打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)，填写部署机可通过 Tailscale 访问的 Host 地址并保存。控制台页面由 Worker Service 动态返回并自动注入 control token；直接用浏览器打开 `worker/ui/index.html` 静态文件不可用。页面可以看到：

- Worker 执行进程是否运行；
- 当前配置的 Host 地址以及 Host 是否可达；
- 当前 `worker_id` 是否已在该 Host 登记、最后在线时间；
- 是否允许主动 claim 新任务，以及当前运行数 / 动态容量；
- 注册令牌允许接入的 Workspace 范围；
- 运行时、并发数、标签和最近日志。

页面保存配置后会原子写入控制卷。身份、能力、可用模型或注册 Token 变化时会重启执行进程并重新注册；领取开关和最大并发会热更新。每次 Worker 执行进程启动（包括服务启动、手动重启和崩溃后的自动重启）都会先把 claim 置为关闭，即使上次退出前处于开启状态也不会自动恢复；用户必须在控制台点击「开始领取」，或执行 `workerctl claim enable`，之后 Worker 才会按本机 `max_concurrency` 拉取任务。

Worker 必须声明自己支持的 `capabilities` 和 `models`。这里的 capability 与 workflow 节点已有的 `capability` 是同一个值，不存在额外的 `required_worker_capabilities` 字段；只有 runtime、capability、provider 和 model 都匹配时，Host 才会把该节点任务交给 Worker。没有兼容 Worker 时任务保持排队，不会被不兼容的机器领取。

### code 节点执行池（协议 v2）

自足的 workflow code 节点（静态 import 闭包 ⊆ `workspace_libs` + stdlib + `requests`；repo 内置的示例节点全部满足）可以被分派到 Worker：Host 把节点代码文本 + sha256 `code_hash` 与 `workspace_libs` 快照打进 bundle 下发，Worker 在 `velites sandbox wrap` OS 沙箱内执行（内置与自定义节点同一条沙箱路径）。接入方式：

- **code capability 声明**：与 agent 一样写在 `capabilities` 列表里（同一通道，Host 按 capability 匹配，不看 model）；
- **容量**：`max_code_concurrency`（默认 0 = 不领取 code 任务），与 `max_concurrency` 是两个独立池，Host 分开记账、分开强制，长 code 任务不会挤占 agent 容量；code 任务也不占 workspace 级 Agent 并发上限；
- **刻意不做热更**：`max_code_concurrency` 不在热更新字段里——经控制台或 `PUT /api/config` 修改后执行进程会重启，让启动预检重新生效；预检发现 code 容量 >0 而找不到 `velites` 二进制时拒绝启动（退出码 2，不自动重启），避免热开后 code 任务在 Host 侧空转重试；
- **回落语义**：没有声明该 capability 的在线 code Worker 时（探测按 capability 匹配，含 `"*"` 通配），dispatch 直接回落 Host 本地 executor 执行，code 任务不会滞留在队列里等 Worker。

**velites 二进制来源（Worker 自带沙箱）**：Worker 解析 velites 的顺序是「自带副本 `<仓库根>/data/bin/velites` 优先，PATH 兜底」，启动预检与 code 执行共用同一解析逻辑；两处都找不到才 fail-closed。因此 Worker 机器**不需要预装 velites**：

- **Docker 部署**：worker 镜像已内置 velites（`/usr/local/bin/velites`，Dockerfile 的 velites-build 阶段按目标平台构建），无需任何额外动作；
- **裸机/开发部署**（直接跑 `worker.executor`，如 `make dev-worker`）：在**与 Worker 同 OS/架构**的机器上、仓库根执行 `./scripts/ensure-velites.sh --dest data/bin`，脚本按 velites/ 源码指纹决定是否需要 `cargo build --release`（指纹不变的重复执行直接跳过），产物原子安置到 `data/bin/velites`。打包分发时按平台分别构建：把对应平台的 `data/bin/velites` 随仓库（或 worker 代码包）一起带到目标机器即可。macOS 产物用 seatbelt、Linux 产物用 bubblewrap（Linux 主机需可用的 bwrap：setuid 或非特权 user namespace），沙箱后端不可用同样 fail-closed。

**secret 边界**：节点 secret（vault 解出的连接凭据）只在 claim 响应里经既有 HTTPS 通道注入——落库的 manifest 与 bundle 都不含 secret；Worker 仅内存持有、经 stdin 传给沙箱子进程，任何持久化前强制剔除（`strip_secret_config`），secret 不接触 Worker 文件系统与日志。随 manifest 下发的 settings 快照按 section 白名单过滤（`node_safe_settings_config`）——白名单当前为空（`NODE_SETTINGS_CONFIG_SECTIONS = ()`，业务 section 已随业务节点迁出），vault/auth/database/agent_workers 等实例级 section 不落库、不下发、不进沙箱 stdin。

**协议兼容**：当前协议版本为 v2（新增 `kind: "code"` claim 与 heartbeat 取消 body）。注册时声明 `max_code_concurrency > 0` 必须是 v2（v1 注册带 code 容量会被 400 拒绝；claim 评估对存量行再查一次协议版本兜底）；v1 Worker 在 v2 Host 上保持 agent-only（收不到 code claim，heartbeat 仍是空 204）；v2 Worker 对 v1 Host 自动降级为 agent-only。Host 的 `min_protocol_version` 仍为 1。

节点的 provider、model、thinking 和 prompt 可以继续在 workflow 编辑器中修改。只修改这些运行配置会更新当前 revision，而不会创建新版本；已创建但尚未领取的 Job 会在领取时使用其 revision 的最新运行配置。任务一旦领取，就固定使用领取时下发的配置。

### 控制面鉴权

Worker Service 启动时在状态卷生成（或复用）`/var/lib/agent-legion-worker-control/control_token`（权限 0600）。除 `GET /api/health` 外，所有 `/api/*` 端点都要求 `Authorization: Bearer <token>`。`workerctl` 按以下顺序取 token：`--token` 参数 > `AGENT_WORKER_CONTROL_TOKEN` 环境变量 > 状态目录下的 `control_token` 文件（容器内执行时自动命中）。

如果需要从终端查询或自动化，可使用容器内 CLI：

```bash
docker compose -f deploy/compose.worker.yaml exec worker workerctl status
docker compose -f deploy/compose.worker.yaml exec worker workerctl claim status
docker compose -f deploy/compose.worker.yaml exec worker workerctl claim enable
docker compose -f deploy/compose.worker.yaml exec worker workerctl claim disable
docker compose -f deploy/compose.worker.yaml exec worker workerctl capacity
docker compose -f deploy/compose.worker.yaml exec worker workerctl capacity 8
docker compose -f deploy/compose.worker.yaml exec worker workerctl config
docker compose -f deploy/compose.worker.yaml exec worker workerctl logs --limit 100
docker compose -f deploy/compose.worker.yaml exec worker workerctl --json logs --limit 100
docker compose -f deploy/compose.worker.yaml exec worker workerctl restart
```

`claim enable/disable` 和 `capacity <数量>` 都是热更新，不会重启执行进程，也不会中断已领取任务；新的容量会在下一次 claim 时同步到 Host 并即时生效（无需重新注册）。所有查询命令均可配合全局 `--json` 输出机器可解析的 JSON；读操作超时 5 秒，`configure`/`restart` 等变更操作超时 60 秒（服务端停止预算约 25 秒）。

也可以直接访问仅限本机的查询接口（先取出 token）：

```bash
TOKEN=$(docker compose -f deploy/compose.worker.yaml exec worker cat /var/lib/agent-legion-worker-control/control_token)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/status
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/config
curl -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8787/api/logs?limit=100'
```

CLI 修改配置的示例（`configure` 是部分更新：只覆盖显式传入的字段，未指定的字段保持现状，`--host-url`/`--worker-id` 均非必填）：

```bash
docker compose -f deploy/compose.worker.yaml exec worker workerctl configure \
  --host-url http://192.0.2.1:8000 \
  --worker-id remote-worker-1 \
  --name 'Remote Worker' \
  --runtime pi \
  --max-concurrency 10 \
  --capability subtitle_review \
  --model openai/gpt-5.2 \
  --register-token-file /run/secrets/agent_worker_register_token \
  --label os=linux --label arch=arm64
```

`--register-token-file` 在 Worker 本机读取 Host 签发的 token，再通过 loopback 控制 API 写入权限为 0600 的状态文件；不要把 token 明文放进命令参数或 shell 历史。对于树莓派、云服务器等无显示器设备，上述 `workerctl` 命令覆盖初始化、状态检查、能力和模型声明、动态扩容、claim 开关、日志与进程重启，不依赖浏览器。

### 崩溃重启与失败状态

Host 暂时不可达或返回 5xx 时，执行进程会保持运行并在进程内指数退避重试注册，不会打印 traceback，也不会触发 supervisor 重启。执行进程因其他原因崩溃后按指数退避自动重启：5 秒起步、每次 ×2、封顶 300 秒；稳定运行满 60 秒后重置退避。退出码 2（Host 明确拒绝注册、Worker 已被吊销，或启动预检失败——例如声明了某个 runtime 但其二进制在自带副本与 PATH 上都找不到）不自动重启，进入 failed 状态，需修正配置后手动 `workerctl restart`。`status` 中的 `restart_count`、`next_restart_delay`、`failed` 字段反映这些状态；容器 healthcheck 会把 failed 或已配置但进程未运行视为 unhealthy。

### 挂载配置与状态副本不一致

首次启动把 `--config` 挂载的 YAML 导入状态卷后，再修改挂载文件不会自动生效。`status` 的 `mounted_config_diverged` 为 true 表示两者内容已分叉，处理路径二选一：

- 用 `workerctl configure`（或控制台）把新值写入状态副本并重启执行进程；
- 或删除状态卷中的 `worker.yaml` 后重启容器，重新导入挂载配置。

默认端口只绑定宿主机 loopback；compose 网络内其它容器可达 `http://worker:8787`，但所有端点（除 `/api/health`）都要求 control token。需要从 Tailnet 上的另一台管理机访问时，显式设置 `AGENT_WORKER_UI_BIND`，并先在主机防火墙或 Tailnet ACL 中限制来源；不要把控制面暴露到公网。

### 开发 worktree 的本地 Worker 检查单

在开发 worktree 里起本地栈（`make dev-up`，或分开 `make dev-backend` + `make dev-worker`）时，job 一直停在 `queued` 或秒败，按顺序查这三处——`scripts/init-worktree.sh` 已尽量自动化，但各自有时机前提：

1. **Workspace 调度默认暂停**：后端每次启动都把全部 workspace 重置为暂停（刻意设计，防止重启后任务不受控自跑），unknown workspace 也默认暂停。init 脚本的「恢复 workspace 调度」步骤只在后端已建表 seed 之后才能生效；如果你是先 init 后首次启动后端，**启动后重跑一次 init 脚本**（幂等），或在 workspace 控制台手动恢复。症状：workflow worker 日志每 3 秒一轮但 `jobs=0`。
2. **Worker 未声明 capabilities/models**：claim 按「runtime + capability + model」三元组逐 Worker 匹配，未声明即判「无 Worker 可认领」，job 秒败并带 `not declared by any Worker` 错误。init 脚本会从基准 worktree 种子 `config/agent-worker.yaml`（含完整声明）；注意生效配置是状态副本 `data/agent-worker-service/worker.yaml`，首次导入后改 config 文件不生效，要走控制台或 `PUT /api/config`。
3. **`claim_enabled` 默认 false**：Worker 每次启动/重启都先关闭 claim（只注册心跳、不领任务），症状是后端日志没有任何 `POST /api/agent-executions/claim`。经 worker 控制台或 `PUT /api/config`（`{"claim_enabled": true}`，热字段立即生效）打开。

## 6. 验证两个 Worker

可以直接查看 Worker 控制台，也可以在部署机执行（`GET /api/agent-workers` 要求登录用户会话，需携带登录 cookie 与 CSRF header，否则会返回 401；也可以直接在 Host Web UI 中查看）：

```bash
curl -b <登录 cookie> -H 'X-CSRF-Token: <csrf-token>' http://192.0.2.1:8000/api/agent-workers
```

响应中应同时看到 `host-local-1` 和 `remote-worker-1`。每个 Worker 还带 `allowed_workspaces`：为空（展示为「全部」）表示用全局 token 注册、可承接所有 workspace 的任务；否则列出 scoped token 授权的唯一 workspace。提交工作流后，Job 详情会分别显示逻辑 `agent_id` 和实际承接任务的 `worker_id`。

并发只受两层约束：每个 workspace 的 Agent 并发上限，以及各 Worker 本机的 `max_concurrency`。workspace 级上限在 workspace 设置页的「Agent 并发上限」配置（随主保存按钮一起保存），对该 workspace 的全部 Agent 节点统一生效——不再按节点单独设置。例如上限 20、三个 Worker 各 10 时，该 workspace 最多并行 20 个 Agent 执行，不要求三个 Worker 都跑满。Worker 只能 claim 其 `allowed_workspaces` 范围内 workspace 的任务。控制台修改 `max_concurrency` 会热生效，无需重启；调低容量不会终止在途任务，而是在运行数降到新上限以下前停止继续 claim。关闭「任务领取」同样只阻止新 claim，不影响已经领取的任务。code 节点任务是独立的第二个池：只受 Worker 本机 `max_code_concurrency` 约束（不占 workspace 级 Agent 上限），且刻意不热更——修改后执行进程重启并经启动预检（见 §5「code 节点执行池」）。

## 7. Tailnet 冒烟验证（上线前必须执行）

Tailscale 由宿主机管理，容器不内嵌 Tailscale。上线前必须从 **Worker 容器内部**分别验证 Host API 和 LLM gateway 的 Tailnet 地址可达——Docker Desktop 的网络命名空间不一定继承宿主机 Tailnet 路由。

在 Worker 机器上执行：

```bash
# Host API（Tailnet 地址）
docker compose -f deploy/compose.worker.yaml exec worker \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://192.0.2.1:8000/api/health', timeout=5).read())"

# LLM gateway（Tailnet 地址 + token；token 未启用时去掉 header）
docker compose -f deploy/compose.worker.yaml exec worker \
  python3 -c "import urllib.request; req = urllib.request.Request('http://192.0.2.1:8788/v1/chat/completions', data=b'{\"model\":\"<model>\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}', headers={'Content-Type': 'application/json', 'Authorization': 'Bearer $LLM_GATEWAY_TOKEN'}); print(urllib.request.urlopen(req, timeout=30).status)"
```

两条都成功后才允许承接生产任务。如果容器内无法解析或路由到 Tailnet 地址，不要把它隐式塞进业务容器——先单独设计 Tailscale sidecar，再重新验证。

## 8. 停止服务

部署机：

```bash
make stack-host-down
```

Worker 机器：

```bash
make stack-worker-down
```

也可以在任何一台机器上用 `make stack-down` 同时停止两个 stack，用 `make stack-status STACK=host|worker` 查看容器与健康状态。停止命令不会删除命名卷中的 PostgreSQL 或运行数据。
