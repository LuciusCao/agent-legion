# Agent Legion Host 与 Worker 部署

Agent Legion 把服务拆成两个角色：Host 负责工作流、数据库与任务调度；Worker 负责运行 Agent。即使同一台公司电脑同时承担两个角色，也运行两个独立服务。

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

## 4. Mac mini 准备 Worker 配置

将同一版本的仓库放到 Mac mini，并复制示例配置：

```bash
cp deploy/worker.home.example.yaml deploy/worker.home.yaml
mkdir -p deploy/secrets
```

确认 `deploy/worker.home.yaml` 中的 `host_url` 是公司电脑可通过 Tailscale 访问的地址。

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

Worker 启动后会向 Host 注册，然后按本机配置的 `max_concurrency` 拉取任务。

## 6. 验证两个 Worker

在公司电脑执行：

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
