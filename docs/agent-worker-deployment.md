# Agent Legion Host 与 Worker 部署

Agent Legion 把服务拆成两个角色：Host 负责工作流、数据库与任务调度；Worker Service 负责本机配置、状态查询和运行 Agent。即使同一台公司电脑同时承担两个角色，也运行两个独立服务。

Worker Service 宿主机发布面默认只绑定 `127.0.0.1:8787`（控制台和查询 API）；compose 网络内其它容器可以到达该端口，但除 `GET /api/health` 外所有端点都要求本地 control token 鉴权。首次启动会把现有只读 YAML 导入独立的可写控制卷；此后可以直接在页面中修改配置，不再需要编辑 YAML。注册密钥仍然只通过 Docker secret 提供，页面和 API 都不会读取或返回密钥内容。

LLM gateway 是独立基础设施，不属于 Agent Worker 协议。Worker 容器通过挂载自己的 Pi 配置访问它。

## 1. 公司电脑准备密钥

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

## 2. 公司电脑准备挂载目录

设置 Agent skills 和 Pi 配置目录。Pi 配置中继续使用已经验证可用的 `gateway/your-model-b` 与 LLM gateway 地址。

```bash
export AGENT_SKILLS_DIR="$PWD/skills"
export PI_CONFIG_DIR="$HOME/.pi/agent"
```

如果 Mac mini 要通过 Tailscale 访问 Host，将监听地址设为公司电脑的 Tailscale IP：

```bash
export AGENT_LEGION_HOST_BIND=192.0.2.1
```

如果 LLM gateway 绑定了 Tailnet 地址并设置了 `LLM_GATEWAY_TOKEN`（绑定非 loopback 地址时必须设置），Worker 容器也需要同一个 token 才能调用 gateway。Compose 通过环境变量透传它，把 token 写进 `deploy/.env`（该文件已被 `.gitignore` 与 `.dockerignore` 排除）或导出到 shell：

```bash
echo 'LLM_GATEWAY_TOKEN=<gateway-token>' >> deploy/.env
chmod 600 deploy/.env
```

不要把它写进 Compose YAML、worker.yaml 或命令行。Worker supervisor 会把它透传给 Pi 子进程；中台凭证本身只存在于 gateway 进程，不经过 Worker。

## 3. 启动公司电脑的 stack

```bash
make stack-host-up
curl http://192.0.2.1:8000/api/health
```

该命令启动 PostgreSQL、Host 和公司电脑本地 Worker。它们使用 [compose.host.yaml](../deploy/compose.host.yaml) 编排。

## 4. Mac mini 准备 Worker

将同一版本的仓库放到 Mac mini，并准备注册密钥目录：

```bash
mkdir -p deploy/secrets
```

无需先复制或编辑 Worker YAML：首次启动会导入仓库内的引导配置，随后在本机控制台填写 Host 地址、Worker ID 和能力。已有 `deploy/worker.home.yaml` 的机器可继续使用；启动前设置 `AGENT_WORKER_CONFIG=./worker.home.yaml`，Worker Service 会在首次启动时导入它。

Worker 的注册 token 决定它能进入哪些 workspace——**token 即 scope**，`worker.yaml` 不需要也不允许声明 workspace。两种 token：

- **全局 token**：把公司电脑的 `deploy/secrets/agent_worker_register_token` 安全复制到 Mac mini 的同一路径；不要把它提交到 Git。Worker 注册后可承接全部 workspace 的任务。
- **Scoped token（需要把 Worker 隔离到单个 workspace 时使用）**：在公司电脑上用全局管理 token 签发，明文只返回一次：

```bash
curl -sS -X POST http://192.0.2.1:8000/api/agent-register-tokens \
  -H "X-Agent-Worker-Register-Token: $(cat deploy/secrets/agent_worker_register_token)" \
  -H 'Content-Type: application/json' \
  -d '{"workspace_id": "video_knowledge", "label": "home-mac-mini"}'
# => {"token_id": "...", "register_token": "<明文，只返回这一次>", "workspace_id": "video_knowledge", "label": "home-mac-mini"}
```

把返回的明文保存为 Mac mini 上的 `deploy/secrets/agent_worker_register_token`（权限 600）。该 Worker 注册后只能看到并 claim `video_knowledge` 的任务。

管理已签发的 scoped token（同样用全局管理 token 鉴权）：

```bash
# 列表（不含明文与 hash，含吊销状态）
curl -sS http://192.0.2.1:8000/api/agent-register-tokens \
  -H "X-Agent-Worker-Register-Token: $(cat deploy/secrets/agent_worker_register_token)"

# 吊销
curl -sS -X POST http://192.0.2.1:8000/api/agent-register-tokens/<token_id>/revoke \
  -H "X-Agent-Worker-Register-Token: $(cat deploy/secrets/agent_worker_register_token)"
```

注意：吊销 scoped token 只影响后续注册；已注册 Worker 落库的 scope 在重新注册前不变。需要立即收缩时，吊销后让该 Worker 重新注册（换用新 token）。

Mac mini 上继续挂载它自己的 Pi 配置，并在 gateway 设置了 token 时同样提供 `LLM_GATEWAY_TOKEN`（见 §2）：

```bash
export PI_CONFIG_DIR="$HOME/.pi/agent"
```

容器内运行的是 Linux，因此 Worker 标签中的 `os: linux` 是有意的；`arch: arm64` 对应 Apple Silicon 容器架构。

## 5. 启动 Mac mini Worker

```bash
make stack-worker-up
make stack-logs STACK=worker
```

打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)，填写公司电脑可通过 Tailscale 访问的 Host 地址并保存。控制台页面由 Worker Service 动态返回并自动注入 control token；直接用浏览器打开 `worker_ui/index.html` 静态文件不可用。页面可以看到：

- Worker 执行进程是否运行；
- 当前配置的 Host 地址以及 Host 是否可达；
- 当前 `worker_id` 是否已在该 Host 登记、最后在线时间；
- 注册令牌允许接入的 Workspace 范围；
- 运行时、并发数、标签和最近日志。

页面保存配置后会原子写入控制卷并重启执行进程，新的配置立即生效。Worker 启动后会向 Host 注册，然后按本机配置的 `max_concurrency` 拉取任务。

### 控制面鉴权

Worker Service 启动时在状态卷生成（或复用）`/var/lib/agent-legion-worker-control/control_token`（权限 0600）。除 `GET /api/health` 外，所有 `/api/*` 端点都要求 `Authorization: Bearer <token>`。`workerctl` 按以下顺序取 token：`--token` 参数 > `AGENT_WORKER_CONTROL_TOKEN` 环境变量 > 状态目录下的 `control_token` 文件（容器内执行时自动命中）。

如果需要从终端查询或自动化，可使用容器内 CLI：

```bash
docker compose -f deploy/compose.worker.yaml exec worker workerctl status
docker compose -f deploy/compose.worker.yaml exec worker workerctl config
docker compose -f deploy/compose.worker.yaml exec worker workerctl logs --limit 100
docker compose -f deploy/compose.worker.yaml exec worker workerctl --json logs --limit 100
docker compose -f deploy/compose.worker.yaml exec worker workerctl restart
```

`--json logs` 输出机器可解析的 JSON；读操作超时 5 秒，`configure`/`restart` 等变更操作超时 60 秒（服务端停止预算约 25 秒）。

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
  --worker-id home-mac-mini-1 \
  --name 'Home Mac mini' \
  --runtime pi \
  --max-concurrency 10 \
  --label os=linux --label arch=arm64
```

### 崩溃重启与失败状态

执行进程崩溃后按指数退避自动重启：5 秒起步、每次 ×2、封顶 300 秒；稳定运行满 60 秒后重置退避。退出码 2（Host 拒绝注册或 Worker 已被吊销）不自动重启，进入 failed 状态，需修正配置后手动 `workerctl restart`。`status` 中的 `restart_count`、`next_restart_delay`、`failed` 字段反映这些状态；容器 healthcheck 会把 failed 或已配置但进程未运行视为 unhealthy。

### 挂载配置与状态副本不一致

首次启动把 `--config` 挂载的 YAML 导入状态卷后，再修改挂载文件不会自动生效。`status` 的 `mounted_config_diverged` 为 true 表示两者内容已分叉，处理路径二选一：

- 用 `workerctl configure`（或控制台）把新值写入状态副本并重启执行进程；
- 或删除状态卷中的 `worker.yaml` 后重启容器，重新导入挂载配置。

默认端口只绑定宿主机 loopback；compose 网络内其它容器可达 `http://worker:8787`，但所有端点（除 `/api/health`）都要求 control token。需要从 Tailnet 上的另一台管理机访问时，显式设置 `AGENT_WORKER_UI_BIND`，并先在主机防火墙或 Tailnet ACL 中限制来源；不要把控制面暴露到公网。

## 6. 验证两个 Worker

可以直接查看 Worker 控制台，也可以在公司电脑执行：

```bash
curl http://192.0.2.1:8000/api/agent-workers
```

响应中应同时看到 `company-local-1` 和 `home-mac-mini-1`。每个 Worker 还带 `allowed_workspaces`：为空（展示为「全部」）表示用全局 token 注册、可承接所有 workspace 的任务；否则列出 scoped token 授权的唯一 workspace。提交工作流后，Job 详情会分别显示逻辑 `agent_id` 和实际承接任务的 `worker_id`。

并发只受两层约束：每个 workspace 的 Agent 并发上限，以及各 Worker 本机的 `max_concurrency`。workspace 级上限在 workspace 设置页的「Agent 并发上限」配置（随主保存按钮一起保存），对该 workspace 的全部 Agent 节点统一生效——不再按节点单独设置。例如上限 20、三个 Worker 各 10 时，该 workspace 最多并行 20 个 Agent 执行，不要求三个 Worker 都跑满。Worker 只能 claim 其 `allowed_workspaces` 范围内 workspace 的任务。

## 7. Tailnet 冒烟验证（上线前必须执行）

Tailscale 由宿主机管理，容器不内嵌 Tailscale。上线前必须从 **Worker 容器内部**分别验证 Host API 和 LLM gateway 的 Tailnet 地址可达——Docker Desktop 的网络命名空间不一定继承宿主机 Tailnet 路由。

在 Mac mini 上执行：

```bash
# Host API（Tailnet 地址）
docker compose -f deploy/compose.worker.yaml exec worker \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://192.0.2.1:8000/api/health', timeout=5).read())"

# LLM gateway（Tailnet 地址 + token；token 未启用时去掉 header）
docker compose -f deploy/compose.worker.yaml exec worker \
  python3 -c "import urllib.request; req = urllib.request.Request('http://192.0.2.1:8788/v1/chat/completions', data=b'{\"model\":\"gateway/your-model-b\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}]}', headers={'Content-Type': 'application/json', 'Authorization': 'Bearer $LLM_GATEWAY_TOKEN'}); print(urllib.request.urlopen(req, timeout=30).status)"
```

两条都成功后才允许承接生产任务。如果容器内无法解析或路由到 Tailnet 地址，不要把它隐式塞进业务容器——先单独设计 Tailscale sidecar，再重新验证。

## 8. 停止服务

公司电脑：

```bash
make stack-host-down
```

Mac mini：

```bash
make stack-worker-down
```

也可以在任何一台机器上用 `make stack-down` 同时停止两个 stack，用 `make stack-status STACK=host|worker` 查看容器与健康状态。停止命令不会删除命名卷中的 PostgreSQL 或运行数据。
