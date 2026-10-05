# Agent Legion Host 与 Worker 部署

Agent Legion 把服务拆成两个角色：Host 负责工作流、数据库与任务调度；Worker Service 负责本机配置、状态查询和运行 Agent。即使同一台部署机同时承担两个角色，也运行两个独立服务。

Worker Service 宿主机发布面默认只绑定 `127.0.0.1:8787`（控制台和查询 API）；compose 网络内其它容器可以到达该端口，但除 `GET /api/health` 外所有端点都要求本地 control token 鉴权。首次启动会把现有只读 YAML 导入独立的可写控制卷；此后可以直接在页面中修改配置，不再需要编辑 YAML。注册密钥既可以通过 Docker secret 提供，也可以在本机控制台中填写；页面和 API 只接受写入、不回显明文，托管副本以 `0600` 权限保存在控制卷中。

LLM gateway 是独立基础设施，不属于 Agent Worker 协议。Worker 容器通过挂载自己的 Pi 配置访问它。

## 1. 部署机准备密钥

在仓库根目录执行：

```bash
mkdir -p deploy/secrets
openssl rand -hex 32 > deploy/secrets/postgres_password
UV_CACHE_DIR=.uv-cache uv run python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
  > deploy/secrets/vault_master_key
```

把 PostgreSQL 密码写入 pgpass。以下命令中的 `<postgres-password>` 必须替换成 `deploy/secrets/postgres_password` 文件里的值：

```text
postgres:5432:agent_legion:agent_legion:<postgres-password>
```

将这一行保存为 `deploy/secrets/postgres_pgpass`，然后限制密钥文件权限：

```bash
chmod 600 deploy/secrets/postgres_password deploy/secrets/postgres_pgpass deploy/secrets/vault_master_key
```

`vault_master_key` 是实例 vault 的主密钥：必须是 32 字节密钥的 URL-safe Base64 编码（Fernet 格式），所以用上面的 `Fernet.generate_key()` 生成而不是 `openssl rand`——格式不对会在 vault 写入 / `secret_ref` 解析时报 `Vault master key is not a valid Fernet key`。compose 把它挂为 `AGENT_LEGION_VAULT_MASTER_KEY_FILE` 注入 Host（见 `deploy/compose.host.yaml`），`scripts/stack-prod-up.sh` 启动前对它 fail-fast 检查，缺失会直接拒绝启动并指向本节。

## 2. 部署机准备挂载目录

设置 Agent skills、Pi 配置和 velites provider/model registry 目录。不同 runtime
各自拥有模型事实源，Worker 启动时通过 runtime adapter 动态发现。

```bash
export AGENT_SKILLS_DIR="$PWD/skills"
export PI_CONFIG_DIR="$HOME/.pi/agent"
export VELITES_CONFIG_DIR="$HOME/.velites"
```

`$VELITES_CONFIG_DIR/models.json` 至少声明 provider 的 `api`（`openai-completions` 或
`anthropic-messages`）、`baseUrl`、`apiKey` 和模型列表；完整格式见
`docs/architecture/velites-model-registry.md`。建议 `apiKey` 使用 `$ENV` 引用并将文件设为
0600。容器通过 `VELITES_CONFIG_DIR` 只读挂载到 `/root/.velites`。

Docker Worker 使用独立、git-ignored 的 env file 注入这些引用变量。不要误以为 Compose
用于自身插值的 `deploy/.env` 会自动进入容器：复制示例文件，写入
`models.json` 实际引用的变量，并限制权限；也可以用绝对路径覆盖默认位置：

```bash
cp deploy/velites-provider.env.example deploy/velites-provider.env
chmod 600 deploy/velites-provider.env
# 编辑 deploy/velites-provider.env，填入 ANTHROPIC_API_KEY / SQAI_API_KEY 等引用变量
export VELITES_PROVIDER_ENV_FILE="$PWD/deploy/velites-provider.env"
```

两个 Compose 入口都会可选加载该文件；文件不存在时不影响只使用字面 `apiKey` 或
`LLM_GATEWAY_TOKEN` 的部署。凭据只写入该 0600 文件，不写 Compose YAML、Worker 配置
或命令行。

如果 Worker 机器要通过 Tailscale 等 overlay 网络访问 Host，将监听地址设为部署机的 overlay 网络 IP：

```bash
export AGENT_LEGION_HOST_BIND=192.0.2.1
```

原生形态（`make prod-up` 不带 `docker` 参数）没有 compose 端口发布层，对应开关是 `NATIVE_BACKEND_BIND` / `NATIVE_WORKER_BIND`（默认 `127.0.0.1`），设为部署机局域网/overlay 网络 IP 即对其他设备暴露 Host API 与 Worker 控制台；端口对应 `NATIVE_BACKEND_PORT` / `NATIVE_WORKER_PORT`（默认 `8000` / `8787`）。这四个变量由 `native-prod-up.sh` / `native-prod-down.sh` 按「进程环境 > 根 `.env`」两级取值（空值按未配置回落默认）：**常驻配置写进根 `.env`**，换 shell 会话、重启或经 launchd/cron 调起都不会丢失、静默退回 loopback；`export` 只作临时覆盖，始终优先于 `.env`。

```bash
# 根 .env（prod worktree）
NATIVE_BACKEND_BIND=192.0.2.1
NATIVE_WORKER_BIND=192.0.2.1
```

**改 bind/port 的操作顺序：先 `make prod-down`，再改根 `.env`（或 export），最后 `make prod-up`**。`native-prod-up.sh` 每启动一个服务进程就在 `data/native-prod.state` 落一份运行态记录（两个服务的启动 PID 与 bind/port，#894；服务尚未开始监听时也已记下），`native-prod-down.sh` 以它为准定位实例、配置为辅：即使顺序做反（先改了配置再 down），down 也会按记录停掉仍在旧地址运行的实例并提示实际地址；按记录 kill 紧前会校验该 PID 的命令行签名、`--host`/`--port` 与工作目录仍属本 worktree 的实例（不依赖监听 socket），实例已不在即视为记录陈旧，回落按当前配置定位并提示；记录 PID 仍存活却校验不过（可能是 PID 复用）时不发信号、保留记录并告警，请人工核对。记录缺失（旧版本 up 起的实例）同样回落按配置定位，此时须按旧值 down。prod-up 发现记录中的实例仍在另一个 bind/port 运行时拒绝启动，避免双实例；同配置重跑时若记录中的实例已启动但尚未监听（上次 up 被中断），视为已在运行并等待其就绪，不重复启动，超时则提示先 `make prod-down`。

把 bind 从具体地址切到通配（如 `127.0.0.1` → `0.0.0.0`）时，先用旧值 `make prod-down` 停掉旧实例再 `make prod-up`：通配监听能与同端口的具体地址监听并存，`native-prod-up.sh` 检测到通配 bind 的端口上已有其他监听即拒绝启动并列出冲突监听，避免起出连同一个库的双实例（违反单副本约束，症状见 [architecture/deployment.md](architecture/deployment.md) 单副本约束节）。对象存储的端口发布仍由 compose 托管，`AGENT_LEGION_S3_BIND` 对两种形态同样生效：绑定为具体 IP 时 `127.0.0.1` 映射消失，原生后端进程访问 S3 的 `AGENT_LEGION_S3_ENDPOINT`（根 `.env`，默认 `http://127.0.0.1:8333`）需同步指向该地址，或把 `AGENT_LEGION_S3_BIND` 设为 `0.0.0.0` 保住 loopback；远程客户端的 `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 一并指向可达地址（完整说明见 [materials-storage-deployment.md](materials-storage-deployment.md)）。

绑定具体地址后还有两处本地接入要跟着调整（`native-prod-up.sh` 检测到失配会打警告，但不代改——Worker 配置一律走控制台/API，见 §5）：部署机本地 Worker 状态副本的 `host_url` 默认指向 loopback，需在 Worker 控制台改为 `http://<绑定地址>:8000`，否则本地 Worker 会静默退避重试注册、永不成功；本机浏览器访问 Worker 控制台的 `http://127.0.0.1:8787` 同样失效，改用绑定地址。远程 Worker 侧没有额外的网络配置项：register/claim/heartbeat/result 全部走 `host_url` 一个地址，材料、bundle 拉取与产物回传走 Host 按 `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 签发的 presigned URL——Worker 控制台只有 Host 地址一项是协议完备的，S3 可达性由 Host 侧配置决定。

如果 LLM gateway 绑定了 Tailnet 地址并设置了 `LLM_GATEWAY_TOKEN`（绑定非 loopback 地址时必须设置），Worker 容器也需要同一个 token 才能调用 gateway。Compose 通过环境变量透传它，把 token 写进 `deploy/.env`（该文件已被 `.gitignore` 与 `.dockerignore` 排除）或导出到 shell：

```bash
echo 'LLM_GATEWAY_TOKEN=<gateway-token>' >> deploy/.env
chmod 600 deploy/.env
```

不要把它写进 Compose YAML、worker.yaml 或命令行。Pi 可从自己的 `models.json`
插值该变量；velites 可在自己的 `models.json` 中把 `apiKey` 配成
`$LLM_GATEWAY_TOKEN`。上游 LLM provider 凭证本身只存在于 gateway 进程。

### Skill root 上移迁移（`agent-legion` 前缀退役）

skill root 已上移为 `~/.agents/skills`（单一来源
`server/app/skills/skill_roots.py`），compose 挂载点同步上移为
`${AGENT_SKILLS_DIR:-../skills}:/root/.agents/skills:ro`。skill 是 root 下的
本地 in-place git 仓库（唯一模式；#322 起无注册表、无远程 clone 通道、
无缓存缺失 re-clone 自愈）——缓存目录缺失即报错并指引在 skill root 下
创建，`:ro` 挂载下仓库路径必须在挂载树内真实存在。从旧版本（skill 位于
嵌套根 `~/.agents/skills/agent-legion/<group>/<name>`）升级的实例：

1. 重跑 `make import-demo`（默认目标根已改为
   `~/.agents/skills/education-video-problems-generation`），把 demo repo 建到新
   位置（幂等，不覆盖已有改动）。
2. pinned ref 的锁在首次 dispatch 或 `make skills-lock` 时按新位置的仓库
   重新解析（`skill_lock` 的 `repo` 字段仅审计，不再参与解析）。
3. 旧位置的 repo 可保留（作为普通本地目录）或自行清理。

## 3. 启动部署机的 stack

```bash
make stack-host-up
curl http://192.0.2.1:8000/api/health
```

该命令启动 PostgreSQL、Host 和部署机本地 Worker。它们使用 [compose.host.yaml](../deploy/compose.host.yaml) 编排：Worker 被隔离在专用 `worker-ctrl` 网络（host 双挂两个网络，postgres / 对象存储只在默认网络）——worker 控制台 `GET /` 无鉴权，网络隔离保证同 stack 的其它容器不能从 compose 内网提取其控制 token，安全模型不依赖宿主侧端口发布（完整拓扑与出站依赖说明见 §5「控制面鉴权」）。本地对象存储（默认 SeaweedFS，#340；RustFS 为存量逃生舱）是否随 stack 启动由 `AGENT_LEGION_LOCAL_S3`（默认 `auto`）经 `scripts/local-s3-decide.sh` 决策：配置外部 S3 后自动跳过，详见 [materials-storage-deployment.md](materials-storage-deployment.md)。

**velites 二进制前置（#381）**：worker 镜像不含 agent runtime 执行器，启动 stack 前必须先把平台匹配的 velites 二进制放到 `VELITES_BIN`（默认 `../velites-bin/velites`，即仓库平级的 `velites-bin/`）——compose 用 long syntax bind mount，源文件缺失会**拒绝启动**（不会静默建目录）。产物获取与架构匹配见 §5「velites 二进制来源」的 Docker 小节。

### 拉取式部署（v* 镜像发布，#574）

`make stack-host-up` 默认在部署机现场构建镜像（`agent-legion-host:local`）。host 镜像跟随仓库 `v*` 发版 tag 走（与 worker 的独立 `worker-v*` 版本线不同：`v*` = 仓库 + host 一体发布，`worker-v*` = 可独立重发的执行端）：push `v<数字>*` tag（如 `v0.7.8`；触发器是 char class `v[0-9]*`，刻意排除 `velites-v*` 等同前缀家族）触发 [host-image-release](../.github/workflows/host-image-release.yml) workflow——原生 runner 构建 linux/amd64 与 linux/arm64（不使用 QEMU），按 digest 合成 manifest list 后推送 GHCR `ghcr.io/luciuscao/agent-legion-host`，打 `<版本>` / `sha-<短哈希>` / `latest` 三个 tag（`sha-` 指向 tag 背后的 commit，annotated tag 亦正确 dereference）。`latest` 只在本次 tag 是远端最高版本时移动：多 tag 并发构建乱序完成、或为修复重推旧 tag 重跑，都不会把 `latest` 指回旧 manifest（此时只打版本 tag 与 `sha-<短哈希>`）。

部署机侧：复制 `deploy/compose.host.pull.example.yaml` 为 `deploy/compose.local.yaml`（Makefile 的 stack-host-* 目标自动并入），把 `image` 改成固定版本 tag，之后 `make stack-host-up` 即拉取启动（override 用 `!reset` 清除 build 段；只覆盖 host 服务，postgres / 对象存储 / worker 沿用基础文件）。GHCR 包默认 private——部署机先 `docker login ghcr.io`（具 read:packages 的 PAT），或在 GitHub package 设置中改为 public。发布走 GitHub 托管 runner，只能推 GitHub 侧 registry；需要内网私有 registry 时须自建 runner，不在本管道覆盖范围内。

与 PR 门的分工：quality-gate 的 docker-build job 只做构建验证（push: false、仅 amd64）；实际发布只由 `v*` tag 触发。注意 `v*` tag 从此有重副作用（触发镜像构建）：若 tag 已打但构建失败，修复后重推同 tag 即可（cache-to 落在 tag ref 下，重跑可复用缓存）。

## 4. Worker 机器准备

将同一版本的仓库放到 Worker 机器，并准备注册密钥目录：

```bash
mkdir -p deploy/secrets
```

无需先复制或编辑 Worker YAML：Worker 首次启动为未配置状态，直接在本机控制台填写 Host 地址、Worker ID 等即可生效（issue #323 后 `--config` 仅作可选 bootstrap）。已有引导 YAML（如复制自 `deploy/worker.remote.example.yaml`）的机器可继续使用；启动时经 `--config ./<your-worker>.yaml` 传入（compose 部署设置 `AGENT_WORKER_CONFIG=./<your-worker>.yaml`），Worker Service 会在首次启动时导入它。

Worker 的注册 token 决定它能进入哪些 workspace——**token 即 scope**，`worker.yaml` 不需要也不允许声明 workspace（issue #35 后全局 token 已退役，只保留 scoped token 一种）：

- **Scoped token（唯一方式）**：在 Host Web UI 的 workspace「设置 → Agent 与 Worker」页面签发与管理：填写 Key 名称即可创建（固定绑定当前 workspace），明文 token 只显示一次。复制后到 Worker 机器的 Worker 控制台（`http://<worker>:8787` 配置页）「Workspace 访问（Scoped Token）」区块粘贴添加；无显示器的设备可用 `workerctl configure --register-token-file <file>` 导入。一个 Worker 可添加多个不同 workspace 的 token：注册时全部呈现，Host 取并集作为 scope；任何一个 token 失效（已删除/未知）都会让整次注册 401。该页面同时支持删除已签发的 key 与清理已注册 Worker 的记录：**删除 key 是切断 Worker 访问的唯一方式**——删除即级联，仅绑定该 key 的 Worker 记录一并删除、凭证立即失效，持有其它 key 的 Worker 同步收窄 scope——没有单独的「吊销 Worker」操作，正如作废 API key 才能作废它的全部客户端。

  该 Worker 注册后只能看到并 claim 对应 workspace 的任务；Host 侧每个 workspace 的设置页也只显示用本 workspace token 注册的 Worker（管理员仍可见全量）。

  从旧模型迁移的运维提示：per-worker revoke 已退役，旧版「已吊销（revoked）」的 Worker 记录不再持久生效——只要该 Worker 还持有存活 key，重新注册即恢复正常（列表中显示「已失效（旧版吊销）」只是遗留标记）。要永久切断一个旧 Worker 的访问，必须删除它持有的全部 key（删除 key 即级联切断），只吊销记录是无效的。

  注意：这些管理端点（UI 与下列 curl 共用的 `/api/agent-register-tokens*`、`DELETE /api/agent-workers/{id}`）要求 **admin 会话**调用，必须携带登录 cookie 与 CSRF header（见 Host Web UI 登录后的会话）。Worker 注册本身仍必须凭 scoped token，不受影响。

  也可以用 curl 在部署机上签发（备选方式）。这些请求需要 admin 会话：先在 Host Web UI 登录（或调用 `/api/auth/login`）取得登录 cookie，并在请求中带上 CSRF header，否则返回 401/403。

```bash
curl -sS -X POST http://192.0.2.1:8000/api/agent-register-tokens \
  -H 'Content-Type: application/json' \
  -d '{"workspace_id": "<workspace_id>", "label": "remote-worker"}'
# => {"token_id": "...", "register_token": "<明文，只返回这一次>", "workspace_id": "<workspace_id>", "label": "remote-worker"}
```

```bash
# 列表（不含明文与 hash）
curl -sS http://192.0.2.1:8000/api/agent-register-tokens

# 删除 key（硬删，立即失效）
curl -sS -X DELETE http://192.0.2.1:8000/api/agent-register-tokens/<token_id>
```

注意：删除 scoped token 会级联生效——同一事务内，不再持有任何存活 key 的 Worker 会被一并删除（其凭证立即失效，不需要等它重启或重注册）；仍持有其它存活 key 的 Worker 保留记录，但失效绑定被剔除、落库 scope 同步收窄到剩余 key 的范围。无绑定记录的 legacy Worker 不受级联影响，可在同一页面手动删除。

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

打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)，先完成控制台登录，再填写部署机可通过 Tailscale 访问的 Host 地址并保存。只有 Worker Service 绑定回环地址时页面才自动注入 control token；Compose 容器内部绑定 `0.0.0.0`，即使宿主机只向本机发布端口也需要手动输入控制令牌。直接用浏览器打开 `worker/ui/index.html` 静态文件不可用。页面可以看到：

- Worker 执行进程是否运行；
- 当前配置的 Host 地址以及 Host 是否可达；
- 当前 `worker_id` 是否已在该 Host 登记、最后在线时间；
- 是否允许主动 claim 新任务，以及当前运行数 / 动态容量；
- 注册令牌允许接入的 Workspace 范围；
- 运行时、并发数、标签和最近日志。

页面保存配置后会原子写入控制卷。身份、可用模型或注册 Token 变化时会重启执行进程并重新注册；领取开关和热字段（`max_concurrency` / `max_code_concurrency` / `upload_max_concurrency` / `ramp_up` / `claim_batch_limit`）都会热更新，无需重启。每次 Worker 执行进程启动（包括服务启动、手动重启和崩溃后的自动重启）都会先把 claim 置为关闭，即使上次退出前处于开启状态也不会自动恢复；用户必须在控制台点击「开始领取」，或执行 `workerctl claim enable`，之后 Worker 才会按本机 `max_concurrency` 拉取任务。

Worker 不声明 `capabilities`（issue #284 起 claim 准入不再按 capability 匹配；
worker.yaml 的 `capabilities:` 键已于 issue #452 移除：存量状态副本残留该键时读取即
剥离、进程内告警一次，下次保存配置时从文件清除；`PUT /api/config` 与
`workerctl configure` 不再接受该字段）。`models` 是可选的 runtime-scoped allowlist，不再是
模型事实源。Agent runtime 声明由本机探测推导（issue #254）：启动时按二进制解析
（自带副本 `data/bin/` 优先、PATH 兜底）探测已安装的 runtime 并默认全部启用，
`disabled_runtimes`（控制台「配置 → Agent 运行时」或 `workerctl configure
--disable-runtime`）反选停用。Worker 对每个生效的 runtime 执行其发现 adapter
（velites 使用
`velites models list --json`），最终注册集合 = 发现结果 ∩ allowlist；该 runtime 没有
allowlist 条目时允许其全部发现结果。Agent 任务的准入条件：workspace token 授权、
runtime 匹配、provider/model 命中 allowlist、labels 满足 `requires_labels`。

### 拉取式部署（worker-v* 镜像发布）

`make stack-worker-up` 默认在 Worker 机器现场构建镜像（`agent-legion-worker:local`）。
多机部署可改用发布镜像：向仓库 push `worker-v*` tag（如 `worker-v0.6.0`；
惯例跟随所基于的仓库发版 tag，同版重发加后缀如 `-r2`）触发
[worker-image-release](../.github/workflows/worker-image-release.yml) workflow——
原生 runner 构建 linux/amd64 与 linux/arm64（不使用 QEMU），按 digest 合成
manifest list 后推送 GHCR `ghcr.io/luciuscao/agent-legion-worker`，打 `<版本>` /
`sha-<短哈希>` / `latest` 三个 tag（`sha-` 指向 tag 背后的 commit，annotated
tag 亦正确 dereference）。

Worker 机器侧：复制 `deploy/compose.worker.pull.example.yaml` 为
`deploy/compose.worker.local.yaml`（Makefile 的 stack-worker-* 目标自动并入），
把 `image` 与 `AGENT_WORKER_IMAGE_VERSION` 改成固定版本 tag，之后
`make stack-worker-up` 即拉取启动（override 用 `!reset` 清除 build 段）。
**拉取镜像不改变任何前置**：velites 二进制外挂、期望 runtime 守卫与配置
挂载同本地构建形态完全一致。GHCR 包默认 private——各 Worker 机器先
`docker login ghcr.io`（具 read:packages 的 PAT），或在 GitHub package
设置中改为 public。发布走 GitHub 托管 runner，只能推 GitHub 侧 registry；
需要内网私有 registry 时须自建 runner，不在本管道覆盖范围内。

与 PR 门的分工：quality-gate 的 docker-build job 只做构建验证（push:
false、仅 amd64）；实际发布只由 `worker-v*` tag 触发。协议升级顺序
（Host first, Worker second）对镜像形态同样适用——升级即 pull 新版本 tag
并重启容器。

### 一键安装（无仓库机器）

没有仓库克隆的 Worker 机器（如个人 Mac、树莓派）用
[install-worker.sh](../scripts/install-worker.sh) 一键组装独立部署：
拉取 standalone compose（`deploy/compose.worker.standalone.yaml`，按
`worker-v<version>` tag ref——镜像与编排文件版本耦合在同一发布 tag）、
下载 sha256 校验的 velites 二进制（架构自动匹配）、生成引导 `worker.yaml`
与 `models.json` 示例，最后 `docker compose up`：

```bash
curl -fsSL https://raw.githubusercontent.com/LuciusCao/agent-legion/develop/scripts/install-worker.sh \
  | sh -s -- --host-url http://<部署机IP>:8000 --worker-id my-worker-1
```

幂等语义分层：脚本自有资产（compose 文件、velites 二进制、`.env` 的
`AGENT_WORKER_IMAGE` 行）每次刷新到目标版本；**用户资产（`worker.yaml`、
`models.json`、`.env` 其余内容）已存在即跳过、绝不覆盖**——`worker.yaml`
首次启动导入控制卷后以控制台为准，覆盖只会制造 `mounted_config_diverged`。
升级 = 重跑脚本带 `--version <新版本>`（须存在对应的 `worker-v*` 发布 tag）。
细节约束（模型注册表就绪前不启动、`--models-json` 显式安装、
`AGENT_WORKER_UI_BIND`/`AGENT_WORKER_UI_PORT` 端口插值、POSIX sh 管道模式）
见脚本头部注释与 `--help`。

控制台 token 体验（issue #489）：默认 loopback 发布下 token 已自动内嵌页面，
打开控制台即用；仅当把 `AGENT_WORKER_UI_BIND` 改为非回环地址（页面不再内嵌）
时才需手动取一次 token（安装脚本的成功提示与 §「控制面鉴权」的判定矩阵
均含该命令）。该内嵌判定随 **worker 0.7.16** 发布——用 `--version` 安装更旧
版本时仍要求手动输入一次（脚本按实际 `--version` 区分提示）。

与拉取式 override 的取舍：仓库克隆 + `compose.worker.local.yaml` 适合开发/
调试机（能跑 `make stack-*`、随仓库升级）；一键安装适合纯执行节点（只有
Docker、目录自包含）。两者最终形态等价（同一镜像 + 同一挂载面），但
**共用 compose project name（`agent-legion-worker`），同一台机器上互斥**
——一键安装的 up 会 recreate 仓库形态的容器并共享同名卷；要换形态先
`down` 另一边。

### 出网代理（#444）

Worker 默认**直连出网**：service 入口会剥离启动 shell 继承的代理环境变量
（`http_proxy` / `https_proxy` / `all_proxy` 及大写变体）。这是刻意的——生产机上
常见的本机代理进程（Clash/mihomo 等）在订阅刷新或配置重载时会整批掐断在途长连接，
数百路并发的 LLM 流量全挂在同一个代理进程上时，一次重载就是一次分钟级的整段
执行失败（velites 表现为 `unexpected EOF during chunk size line`）。**生产 Worker
不应在本机代理进程之后运行**；开发机上带着代理 shell 启动的 worker.service
会在日志里看到一行「已剥离继承的代理环境变量」。

确需代理出口的部署（例如 provider 只能经网关访问）在控制台「配置 → 高级参数 →
出网代理」或 `worker.yaml` 的 `proxy:` 字段显式声明，支持 `http://` / `https://` /
`socks5://` / `socks5h://` URL（可含认证信息）。填写后 executor 与全部 agent
子进程的出网流量（backend 上传 + LLM）统一经该代理；留空或删除即回直连。该字段
是进程级配置，修改后随执行进程重启生效，不做热更新。


### 冷启动容量爬坡（ramp-up，#471）

冷启动窗口（发布重启、`claim_enabled` false→true、大批量 run 提交后恢复调度）会把积压的 queued 请求一次性释放——数百个 agent 同时发起首次大模型请求，瞬时压力打满 provider。`ramp_up` 配置块（控制台「配置 → 高级参数 → 容量爬坡」或 `PUT /api/config` 的 `ramp_up` 键，热更新免重启）把释放节奏改为阶梯放量：

- **参数**：`initial` / `step` / `interval_seconds`（缺省 1 / 1 / 60s）——生效容量从 `initial` 起步、每 `interval_seconds` 放开 `step` 档，到 `max_concurrency` 目标后窗口永久关闭、回归正常容量语义。空对象 `{}` 是最保守爬坡；`null` 或缺块 = 禁用（一次性全量，即旧行为）。
- **只升不降**：窗口内热更到更小的 `initial` 不回撤在途档位（避免与完成流耦合振荡）；`claim_enabled` 关闭期间爬坡虚拟时钟不前进——停领一小时的 Worker 恢复后不会直接跳到高档。
- **生效范围仅冷启动窗口**：稳态的完成/补领槽位波动不经过状态机；爬坡只钳制新领取的预算，已在跑的执行不受影响。
- 控制台容量卡在爬坡期显示「容量爬坡中 e/t」进度；未勾选提交 `null` 即时禁用。与 claim pacing（#472）正交：pacing 管两次 claim 之间的等待，爬坡管本 pass 最多领多少。

### code 节点执行池（协议 v2）

自足的 workflow code 节点（静态 import 闭包 ⊆ `workspace_libs` + stdlib + `requests`；repo 内置的示例节点全部满足）可以被分派到 Worker：Host 把节点代码文本 + sha256 `code_hash` 与 `workspace_libs` 快照打进 bundle 下发，Worker 在 `velites sandbox wrap` OS 沙箱内执行（内置与自定义节点同一条沙箱路径）。接入方式：

- **容量**：`max_code_concurrency`（默认 0 = 不领取 code 任务），与 `max_concurrency` 是两个独立池，Host 分开记账、分开强制，长 code 任务不会挤占 agent 容量；code 任务也不占 workspace 级 Agent 并发上限。code 任务的准入只需要协议版本 ≥ v2、code 池有余量、workspace 在 token 授权范围内，无需任何 capability 声明（issue #284）。code 沙箱包装器（`velites-sandbox`）自 #383 起内置在 worker 镜像里——code 池不依赖外挂 velites，纯 pi worker 或什么都不挂的 worker 也能开 code 池；host 侧的 code 本地兜底在 docker 形态下禁用（详见下文 velites 小节的 host 说明）；
- **热更新**：`max_code_concurrency` 与 `max_concurrency` 一样经控制台或 `PUT /api/config` 热生效，不重启执行进程、不打断在跑执行；调大立即放行新 claim，调小在运行数降到新上限以下前停止继续 claim。唯一例外是 0→>0 的热开启要求本机可解析 `velites` 二进制（启动预检的同一道 fail-closed 守卫，EXEC-CODE-003）：缺失时循环内拒绝热开并打日志提示，装好 velites 后下一轮循环自动生效，避免热开后 code 任务在 Host 侧空转重试；
- **回落语义**：没有在线 code Worker（协议 ≥ v2、code 池有余量、workspace 已授权）时，dispatch 直接回落 Host 本地 executor 执行，code 任务不会滞留在队列里等 Worker。

**velites 二进制来源（Worker 自带沙箱）**：Worker 解析 velites 的顺序是「自带副本 `<仓库根>/data/bin/velites` 优先，PATH 兜底」，启动预检与 code 执行共用同一解析逻辑；两处都找不到才 fail-closed。worker 镜像**不含任何 agent runtime 执行器**（issue #381）——velites 与 pi 都由部署方以外挂二进制提供，本机装什么 runtime 就声明什么：

- **Docker 部署**：从 GitHub Release（`velites-v*` tag，velites-release workflow 产出）下载与宿主机架构一致的 tarball，解出的 `velites` 放到 compose 的 `VELITES_BIN`（默认 `../velites-bin/velites`）——compose 把它 bind mount 到容器内 `/app/data/bin/velites`（自带副本目录，优先于 PATH）。架构必须与 worker 镜像一致（x86_64 取 `*-x86_64-unknown-linux-gnu`，arm64 取 `*-aarch64-unknown-linux-gnu`）；挂载了错误架构的二进制能通过存在性探测，但执行时以 exec format error 失败——期望 runtime 守卫会把它转成启动失败（见下）。**防漏挂载守卫**：compose 默认注入 `AGENT_WORKER_EXPECT_RUNTIMES=velites`（`deploy/.env` 可覆盖：多值逗号分隔；显式置空禁用守卫，零 runtime 注册合法——零 runtime / 纯 code 池形态同时叠加 `deploy/compose.worker.zero-runtime.yaml` override 去掉 velites bind mount，否则无条件挂载会要求准备一个用不上的二进制文件），启动时探测不到期望 runtime、或期望 runtime 模型发现失败（含架构错配）即 fail-fast（退出码 2，supervisor 不自动重启、healthcheck 变 unhealthy）。**pi 在 docker 镜像内不可用**：pi 的入口是 npm 包脚本，依赖 node 运行时与包树，而 #381 已把它们移出镜像——pi 部署走裸机形态（PATH 或 `data/bin/`），需要在 docker 跑 pi 时自行构建含 node+pi 的镜像变体；
- **裸机/开发部署**（直接跑 `worker.executor`，如 `make dev-worker`）：在**与 Worker 同 OS/架构**的机器上、仓库根执行 `./scripts/ensure-velites.sh --dest data/bin`，脚本按 velites/ 源码指纹决定是否需要 `cargo build --release`（指纹不变的重复执行直接跳过），产物原子安置到 `data/bin/velites`。无源码/工具链的纯执行节点可直接取 Release 产物安置到同一目录（此时不要跑 `make prod-up`——见下条升级语义）。**注意不要给 Release 产物手写 `.src-stamp`**：产物不带 stamp、Release 也不发布其构建 tree hash，而 velites 与仓库版本线解耦——产物源码往往旧于本 checkout，手写当前 HEAD 指纹（`git rev-parse HEAD:velites > data/bin/velites.src-stamp`）会给旧二进制伪造新鲜度，使 prod-up 永久跳过重建、启动对账（staleness）不再报漂移，比 #831 更彻底地静默。无 cargo 但需要刷新 `data/bin` 的合法出路只有两条：装 Rust 工具链重建，或在与本机同 OS/架构、同一仓库状态的机器上跑 `ensure-velites.sh --dest data/bin` 后**把二进制与 stamp 一起拷贝**（stamp 与产物同源才可信）。macOS 产物用 seatbelt、Linux 产物用 bubblewrap（Linux 主机需可用的 bwrap：setuid 或非特权 user namespace），沙箱后端不可用同样 fail-closed。裸机部署同样可设 `AGENT_WORKER_EXPECT_RUNTIMES`（如 systemd 单元的 `Environment=`）启用期望 runtime 守卫；不设时保持「探测到什么声明什么」的默认语义。
- **升级语义（#831）**：原生形态 `make prod-up` 对 velites 的两个安置点**都**做新鲜度刷新——先 PATH 模式（`ensure-velites.sh` 默认模式），再 `--dest data/bin`（自带副本，各自按指纹独立跳过/重建）。历史版本只刷 PATH，而「自带副本优先」的解析顺序让 install-deps 首次安置的 `data/bin` 副本永久优先命中，velites 升级静默失效。「安置在哪、什么算新鲜」由部署 planner（`scripts/velites_deploy_plan.py`）从**真实 resolver**（`worker/binary_resolution.py` / `shared/code_sandbox.py` / `worker/runtime/catalog.py`）推导，脚本只做构建与原子安置——bash 不再持有平行的查找模型（四轮 codex 同构 finding 的根源）。PATH 模式的主目标跟随 `which velites` 就地刷新（无 PATH 副本时落 `VELITES_INSTALL_DIR` 或 `~/.local/bin`），并对 PATH 上按名独立发现的家族成员位置（`velites` 与 `velites-sandbox` 可来自不同目录，#835 codex P2）一并刷新。沙箱包装器 `velites-sandbox` 同批刷新（解析候选序它优先于 velites，目录里存在旧包装器时只刷 velites 会让 code 沙箱继续用旧版——与 #831 同构的漂移；判鲜是**家族级**的：velites 本体全新鲜但包装器陈旧同样触发重建，fast-path 不会短路掉它；不存在时不主动创造，裸机默认走 velites 兜底）。重建需要 cargo——新 worktree 的 `data/` 为空，首次 prod-up 必经 `--dest` 构建路径，无 cargo 即 fail-fast（错误提示给出合法出路：装工具链，或从同 OS/架构、同仓库状态的机器连 stamp 一起拷贝；不得给 Release 产物手写当前指纹，见上一条）。启动对账（软告警）覆盖**每个消费角色实际解析到的**二进制：Worker 侧组合 agent runtime 面（自带副本优先解析）与 code 沙箱面（`velites-sandbox` 优先候选序）；Host 侧 lifespan 也对 code 沙箱面（经 `shared` 解析）与本地 agent runtime 面（PATH 裸名）对账——`data/bin` 副本漂移时两侧启动日志都打 WARNING。方向中性（副本可能落后于源码，也可能来自领先版本线的 PATH 共享副本；不 fail-closed，velites 版本线独立，允许刻意落后；无 stamp、stamp 损坏或无 git/源码树的形态无从对账，静默跳过）。对账核心在 `shared/velites_staleness.py`（Host 侧钩子不 import worker 包，两侧共用）。

  运行时监督的灰度/回退开关（同 family 的 stdout 事件泵开关 `AGENT_WORKER_EVENT_PUMP` 一并适用）：`AGENT_WORKER_EXIT_WATCH=kqueue|pidfd|scan|auto`（默认 auto：macOS 选 kqueue、Linux 选 pidfd、都不可用回落 scan——单根 watcher 线程监听全部在飞子进程的退出；显式指定可用即遵守、不可用回落 scan 并打印原因）；`AGENT_WORKER_LANE_IDLE_TIMEOUT=<秒>`（默认 30，执行车道线程的空闲退出时限）。两者只在出现监督面异常时需要动——slots 心跳行会持续打印 `exit <mode>` 与 `lane <n>` 供核对。

**secret 边界**：节点 secret（vault 解出的连接凭据）只在 claim 响应里经既有 HTTPS 通道注入——落库的 manifest 与 bundle 都不含 secret；Worker 仅内存持有、经 stdin 传给沙箱子进程——secret 标记键在 Host 侧 `split_manifest_config` 就不进下发 manifest，Worker 侧没有任何 config 派生数据落盘，secret 不接触 Worker 文件系统与日志。随 manifest 下发的 settings 快照按 section 白名单过滤（`node_safe_settings_config`）——白名单当前为空（`NODE_SETTINGS_CONFIG_SECTIONS = ()`，业务 section 已随业务节点迁出），vault/auth/database/agent_workers 等实例级 section 不落库、不下发、不进沙箱 stdin。

**协议兼容**：当前协议版本为 v5：v3 新增 runtime-scoped model triples；v4 新增
gzip 压缩产物对象（v4+ Worker 收 `.gz` 后缀的上传规格与 `content_encoding` 输入
引用，旧 Worker 保持裸键）；v5 新增 per-Worker 批量心跳（`POST
/api/agent-executions/heartbeats`，一次请求单写事务续期本机全部租约，心跳写流量
按机器数而非槽数）。v2 的 code claim 和 heartbeat 取消 body 语义不变。Host 把旧
Worker 的二元 provider/model 声明解释成 runtime wildcard；新 Worker 总是发送显式
runtime。注册时 code 容量仍只要求协议 >= v2。Host 的 `min_protocol_version` 仍为
1。心跳的混合舰队兼容：单条心跳端点保留且行为不变（旧 Worker 对升级后 Host 语义
零变化）；v5 Worker 对批量路由缺席（404/405）的 Host 自动降级逐条心跳（逐条带 5s
短超时；transient 错误不降级），zombie（进程死且未被上传收养）的租约停跳、交给
Host 孤儿 sweeper 回收。升级必须遵循 **Host first, Worker second**：v3 起注册响应
携带 `host_protocol_version`，新 Worker 若发现 Host 低于自身协议版本（旧响应缺少
该字段也视为旧 Host）会以退出码 2 fail-closed，不进入 claim，避免旧 Host 把
runtime-scoped 模型降成二元 provider/model 后误投到另一个 runtime。确认 Host 健康
后再逐台重启 Worker。**结果上报头（#748）**：`X-Agent-Result` 携带原始 UTF-8
字节（CJK 错误摘要是非 ASCII 头值），Worker → Host 链路上的反向代理 / LB / 网关
必须容忍非 ASCII 头值透传（改写或拒收会导致结果不可投递、租约过期重投）。该线
格式变更（ensure_ascii 字符串 → 原始 UTF-8 字节）不升协议版本，因此升级纪律上
**Host 必须先于 Worker 升级**：反向混编（新 Worker + 旧 Host）时 `error_message`
/ `agent_stderr_tail` 的 CJK 载荷在旧 Host 上按 latin-1 视图显示为乱码（结构与成
败判定不受影响）；回滚同理须 Host/Worker 同退——只退 Host 即构成同一反向混编窗
口，CJK 头值 mojibake（仅可读性受损）。同头受 14 KiB 字节预算约束，超预算时 Worker 按 stderr 尾部 →
error_message → command（纯观测面，清空）→ 产物清单的顺序降级；产物清单面按引用
形态分流——直传 dict 引用抛溢出信号后走「清单进归档」协议（#755）：Worker 把完整
direct-ref 清单写成结果归档首成员 `result-output-artifacts.json`（产物字节已在 S3，
不重复传输），头里只带 `output_artifacts_in_archive` 布尔标记，Host 在结果 commit 时
从归档读回清单（读不回则诚实判败 failed；cancelled 不翻转）；嵌入重写按 claim 下发的
`max_archive_bytes` 在原子替换前重校归档实际大小（清单成员可能把低于但接近上限的原
归档推过 Host 413 大小门禁），超限不重报大归档，走同一诚实判败通道（原归档可提交则
原样保留证据、本身也超限则回收空归档）；CAS 字符串引用
（~78B/条，天然落预算）才走最后手段截断（清单降级为空并打
`output_artifacts_truncated` / `output_artifacts_total` 标记）；CAS 截断形态下产物
字节本来就在归档里，Host 见 truncated 标记跳过「空清单改判 failed」，改从归档暂
存视图判定产物齐全与否。另一直传保护面：直传失败换轨（tar 内嵌产物 + CAS 通道）
前 Worker 按 claim 下发的 `max_archive_bytes`（Host 实例设置实际值，旧 Host 未下发
时回落 64 MiB 默认）做体积预检，超「上限 − 1 MiB 余量」不换轨、本地诚实判败，
避免重内嵌必撞 Host 413 丢结果后的全量重跑循环。

**workflow_key 兼容窗口期（issue #211，截止 2026-10-31）**：claim 响应中的
`workflow_key` 字段已 deprecated（与 `workspace_id` 恒等，schema v62 绑定）。字段
与列将于 2026-10-31 的终态批移除——所有 Host 实例须在窗口期内升级至 ≥ schema v68
（存量 workflow_key 已对齐），Worker 镜像须改为读取 `workspace_id`；仍解析旧字段
的 Worker 在字段移除后将解析失败。升级顺序沿用 **Host first, Worker second**：
v68 及以上的 Host 仍下发 `workflow_key`（兼容窗口内），Worker 可在其后任意时间切换。

节点的 provider、model、thinking 和 prompt 可以继续在 workflow 编辑器中修改。只修改这些运行配置会更新当前 revision，而不会创建新版本；已创建但尚未领取的 Job 会在领取时使用其 revision 的最新运行配置。任务一旦领取，就固定使用领取时下发的配置。

### 控制面鉴权

控制令牌（登录 Worker 控制台）与 workspace 注册 Key（授权 Worker 接入 Host）用途不同。默认 Host Compose 在项目根目录运行以下命令取得控制令牌，粘贴到 Worker 登录框后，再到「配置 → Workspace 访问」添加注册 Key：

```bash
docker compose -f deploy/compose.host.yaml exec worker cat /var/lib/agent-legion-worker-control/control_token
```

独立 Worker 部署将 Compose 文件换成启动时使用的 `deploy/compose.worker.standalone.yaml` 或 `deploy/compose.worker.yaml`，保留相同项目名及其他 Compose 参数。原生部署从 Worker 的 `--state-dir` 目录读取 `control_token`；没有该机器访问权限时由 Worker 维护者完成登录。控制令牌不要放进控制台 URL、Host 配置或注册标签。

Worker Service 启动时在状态卷生成（或复用）`/var/lib/agent-legion-worker-control/control_token`（权限 0600）。除 `GET /api/health` 外，所有 `/api/*` 端点都要求 `Authorization: Bearer <token>`。`workerctl` 按以下顺序取 token：`--token` 参数 > `AGENT_WORKER_CONTROL_TOKEN` 环境变量 > 状态目录下的 `control_token` 文件（容器内执行时自动命中）。

**页面内嵌判定（issue #489）**：控制台页面是否自动内嵌 token，由**实际暴露面**而非进程 bind 决定。Docker 形态下容器内进程必绑 `0.0.0.0`（端口映射前提），但页面真正从哪个地址被访问由 compose 的宿主侧发布地址（`AGENT_WORKER_UI_BIND`）决定——compose 把该值经 `AGENT_WORKER_UI_EFFECTIVE_BIND` 环境变量告知 service（与发布行同一插值源，`.env` 一处改、两处同步）。该机制随 **worker 0.7.16** 发布，且以下方「Host 头校验」已启用为前提：一键安装（`install-worker.sh`）在更低版本上安装时页面仍要求手动输入 token（脚本的成功提示会按实际版本区分）。判定矩阵（进程 bind × 宿主侧发布地址 × token 内嵌结果）：

| 进程 bind（`--host`） | 宿主侧发布（`AGENT_WORKER_UI_BIND`） | token 内嵌 | 说明 |
| --- | --- | --- | --- |
| `127.0.0.1` 等回环 | （无发布层，裸机/dev 形态） | 是 | 历史行为：`AGENT_WORKER_UI_EFFECTIVE_BIND` 未设置，按进程 bind 判定 |
| `0.0.0.0`（容器内） | `127.0.0.1`（默认） / `[::1]` | 是 | 容器内 bind 仅为端口映射前提；宿主发布回环 = 页面仅本机可达，内嵌不扩大风险面，日志打 info 说明判定链。compose.host.yaml 形态另需网络隔离成立（见下方网络拓扑） |
| `0.0.0.0`（容器内） | `0.0.0.0` / `192.0.2.1` 等非回环 | 否 | 同网段浏览器都能打开页面，不内嵌 + warning，需手动输入 token（见下方取 token 命令） |
| 回环 | 非回环 | 否 | 复合形态兜底：设置了 `AGENT_WORKER_UI_EFFECTIVE_BIND` 时判定只看发布面（发布非回环即不内嵌，覆盖发布层与进程 bind 不一致的场景） |

**Host 头校验（issue #923）**：控制面全部路由（`GET /`、`/assets/*` 与全部 `/api/*`，含 `/api/health`）只接受 Host 头属于白名单的请求，其余一律 403。白名单 = 回环变体（`127.0.0.1` / `localhost` / `[::1]`，任意端口）∪ 实际暴露面地址（`AGENT_WORKER_UI_EFFECTIVE_BIND`，未设置时取进程 bind）∪ `AGENT_WORKER_CONSOLE_URL` 的主机名。经主机名（反向代理、MagicDNS 名等）访问控制台时，把该地址写进 `AGENT_WORKER_CONSOLE_URL`。白名单中只要出现非回环主机名（暴露面或控制台地址），页面就不内嵌 token，需手动输入一次。暴露面为通配地址（`0.0.0.0` / `::`）时无法枚举合法主机名，Host 校验不启用、页面也不内嵌 token（API 仍由 control token 把守）。变更类请求（`PUT` / `POST` / `DELETE`）另做来源校验：浏览器带 `Sec-Fetch-Site` 时只放行 `same-origin` / `none`，带 `Origin` 时须与 Host 头一致或等于 `AGENT_WORKER_CONSOLE_URL` 的 origin（反向代理改写上游 Host 的形态）；`workerctl` 等不带这两个头的客户端不受影响。

两个运维注意：

- **compose override 改发布地址时必须同步设置 `AGENT_WORKER_UI_BIND`**：compose 合并 override 时 `ports` 列表整体替换、`environment` 按 key 合并——只在 override 里改 `ports` 发布地址（或加新条目）而不同步 `.env` 的 `AGENT_WORKER_UI_BIND`，两个插值源就会漂移，service 会按旧的 EFFECTIVE_BIND 判定内嵌（页面实际已发布到非回环地址 = 泄漏）或反向多要一次手动 token。仓库内两个 worker compose 的 `ports` 行与 `EFFECTIVE_BIND` 同用 `${AGENT_WORKER_UI_BIND}` 插值，`.env` 一处改两处同步就是为此。
- **网络拓扑差异（`compose.host.yaml` 形态）**：部署机 stack 里 postgres / seaweedfs / rustfs / host / worker 原本同挂一个默认 compose 网络——而 worker 控制台 `GET /` 无鉴权，同网 peer 容器 `curl http://worker:8787/` 即可提取内嵌的 control token（issue #489 讨论里担心的「token 泄给同网段」在 docker 内网上被重新引入）。该 compose 现已把 worker 隔离到专用 `worker-ctrl` 网络：host 双挂默认网络与 `worker-ctrl`（仍是 worker 唯一需要直连的 peer——register/claim/heartbeat/result 与旧 CAS 产物通道都走它），worker 只挂 `worker-ctrl`，postgres/seaweedfs/rustfs 不可达无鉴权的控制台。worker 的材料下载与产物直传本就走 Host 按 `AGENT_LEGION_S3_PUBLIC_ENDPOINT`（宿主发布地址，非 compose 服务名）签发的 presigned URL 且 worker 不持对象存储凭据，网络隔离不改变该通道；但把 `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 覆盖为 compose 服务名（如 `http://seaweedfs:8333`）在这种形态下会不可达——产物直传自动回落经 host 的 CAS 通道，材料任务会失败，覆盖值必须用宿主侧发布地址。无 peer 的 `compose.worker.yaml` / standalone 形态不需要（也从未需要）该隔离。

发布非回环时（后两行），从容器内手动取一次 token，页面会把它存进 localStorage，日常无需重复：

```bash
docker compose exec worker cat /var/lib/agent-legion-worker-control/control_token
```

裸机/dev 形态不设 `AGENT_WORKER_UI_EFFECTIVE_BIND` 时行为与历史版本完全一致（按进程 bind 判定）。issue #489 讨论中的另两个方向——把控制面降级为 loopback-only + 经 ssh/socat 转发访问（消灭网络暴露面，代价是远程管理变麻烦）、以及重新审视 control token 本身换更强的本机身份绑定（如 Unix socket / 进程属主校验）——暂缓（deferred），不在当前实现范围内；需要时回到该 issue 继续讨论。

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
  --disable-runtime velites \
  --max-concurrency 10 \
  --model openai/gpt-5.2 \
  --register-token-file ./marketing.token \
  --label os=linux --label arch=arm64
```

`./marketing.token` 是本机保存的 scoped token 文件（从 Host Web UI 复制明文后以 0600 权限保存，文件名自定）；compose 不再挂载全局 token。

`--register-token-file` 在 Worker 本机读取 Host 签发的 scoped token，再通过 loopback 控制 API 写入权限为 0600 的状态文件（与控制台「添加并验证」同构）；不要把 token 明文放进命令参数或 shell 历史。重复执行可添加多个 workspace 的 token。对于树莓派、云服务器等无显示器设备，上述 `workerctl` 命令覆盖初始化、状态检查、模型声明、动态扩容、claim 开关、日志与进程重启，不依赖浏览器。

### 崩溃重启与失败状态

Host 暂时不可达或返回 5xx 时，执行进程会保持运行并在进程内指数退避重试注册，不会打印 traceback，也不会触发 supervisor 重启。执行进程因其他原因崩溃后按指数退避自动重启：5 秒起步、每次 ×2、封顶 300 秒；稳定运行满 60 秒后重置退避。退出码 2（Host 明确拒绝注册——例如持有的全部 key 已被删除，或启动预检失败——例如 `max_code_concurrency > 0` 但沙箱包装器（velites-sandbox 或 velites）在自带副本目录与 PATH 上都找不到；docker 形态该包装器内置镜像，此错误通常意味着镜像损坏）不自动重启，进入 failed 状态，需修正配置后手动 `workerctl restart`。`status` 中的 `restart_count`、`next_restart_delay`、`failed` 字段反映这些状态；容器 healthcheck 会把 failed 或已配置但进程未运行视为 unhealthy。

### 挂载配置与状态副本不一致

首次启动把 `--config` 挂载的 YAML 导入状态卷后，再修改挂载文件不会自动生效。`status` 的 `mounted_config_diverged` 为 true 表示两者内容已分叉，处理路径二选一：

- 用 `workerctl configure`（或控制台）把新值写入状态副本并重启执行进程；
- 或删除状态卷中的 `worker.yaml` 后重启容器，重新导入挂载配置。

默认端口只绑定宿主机 loopback；compose 网络内其它容器可达 `http://worker:8787`，但所有端点（除 `/api/health`）都要求 control token；`compose.host.yaml` 形态下同 stack 的其它服务更被网络隔离挡在控制台之外（见 §「控制面鉴权」的网络拓扑说明），只有 host 容器可达。需要从 Tailnet 上的另一台管理机访问时，显式设置 `AGENT_WORKER_UI_BIND`，并先在主机防火墙或 Tailnet ACL 中限制来源；不要把控制面暴露到公网。非回环发布下控制台不再内嵌 token（判定矩阵见 §「控制面鉴权」），改用 `docker compose exec worker cat /var/lib/agent-legion-worker-control/control_token` 手动取。

### 全新克隆的本地 Worker（无 init-worktree.sh）

外部用户从干净克隆起步时没有 init-worktree.sh 的种子自动化，`make dev-up`
只在 worker 状态副本 `data/agent-worker-service/worker.yaml` 存在时才会启动
Worker（issue #323 后 dev 侧不再有 `config/agent-worker.yaml` 种子）。
`make install`（install-deps.sh）已自动写入最小 dev 配置；未跑过时的手工步骤：

1. 写入最小状态副本 `data/agent-worker-service/worker.yaml`（0600），含
   `host_url`（dev 栈后端端口，默认 `http://127.0.0.1:8001`）、`worker_id`、
   `name`、`work_root: data/agent-worker`；其余字段（如 `models` allowlist，
   留空表示允许 runtime 发现的全部模型）之后走 Worker 控制台/API 配置。
2. 起后端并登录 Host Web UI，在 workspace「设置 → Agent 与 Worker」为目标
   workspace 签发 scoped token；到 Worker 控制台（默认 `http://127.0.0.1:8789`）的
   「Workspace 访问（Scoped Token）」区块粘贴添加。Worker 侧 token 随时可以
   补——注册失败只影响 Worker 自身，不需要重启后端。该设置页顶部的
   「Worker 与 Worker 控制台」卡片、签发成功后的「下一步」以及各处 Worker
   列表空态都带「打开 Worker 控制台」入口：地址来自后端 env
   `AGENT_LEGION_WORKER_CONSOLE_URL`（`make dev-up` 按 Worker 端口自动注入，
   `native-prod-up.sh` / Host compose 注入 `:8787`；Worker 控制台经其它地址
   暴露时在 `.env` 显式配置，显式留空则不显示链接）。回环地址只能在 Worker
   所在机器的浏览器里打开，链接的悬停提示会说明这一点。Worker 注册成功后，
   主控制台每一行 Worker 还会带该 Worker **自报**的「控制台」链接：Worker
   原生 Service 按自己的控制面绑定地址推导（通配绑定 `0.0.0.0` 回落
   `127.0.0.1`，IPv6 `::` 回落 `[::1]`），经环境变量 `AGENT_WORKER_CONSOLE_URL` 交给 executor，注册时
   补充 labels 的可选键 `console_url`（`worker/console_url.py`）。控制台经反向
   代理或映射到非回环地址时，在 Worker 侧显式设置该变量。三份 Compose 均要求
   在部署环境中显式配置浏览器可达 URL（例如 `https://worker.example/console`），
   不从 `AGENT_WORKER_UI_BIND` 猜测；缺省或显式空串均不自报。旧版
   Worker 不上报，对应行只保留部署级入口。
   已配置的 `console_url` 与其他自定义标签始终原样保留（可能用于 `requires_labels` 调度），环境地址不覆盖它。
   自定义标签已经占满 32 项，或自报 URL 超过 256 字符时，
   跳过该可选标签并保留原标签，避免控制台入口使 Worker 注册失败；不截断 URL。
   入口通过已登录用户可读的 `GET /api/agent-workers/console` 获取部署地址，
   不下载 Worker 清单；workspace 状态列表的请求与缓存均按 workspace 隔离。
   首次地址请求失败显示可重试错误，只有成功返回空地址才表示未配置；
   后台刷新失败保留最近成功的配置，后续成功响应（包括清空配置）替换缓存。
   非空配置必须是绝对 HTTP(S) 地址，支持 IPv6、反向代理路径与 query；
   空白、反斜杠、非法端口或 URL 用户名/密码会在创建服务前报错，诊断不回显原值。
   地址是公开导航信息，控制令牌应在 Worker 控制台中输入，不放进该地址。
   dev/native 脚本只设置内部 `AGENT_LEGION_WORKER_CONSOLE_DEFAULT_URL`；
   后端使用同一 dotenv 解析器完成加载后，按进程环境 → 根 `.env` → 脚本默认值
   选择 `AGENT_LEGION_WORKER_CONSOLE_URL`，显式空值始终有效，shell 不另行解析 `.env`。
3. 重跑 `make dev-up`（幂等）启动 Worker，然后在 worker 控制台打开
   `claim_enabled`（默认关闭，见下方检查单第 3 条）。

### 开发 worktree 的本地 Worker 检查单

新 workspace 引导只有在 workflow 已发布、所需 Worker 已就绪且调度确认运行后才解锁添加任务。暂停状态未加载或请求失败时不推断为暂停，也不显示确定性的阻塞警告；纯 code workflow 不要求接入 Worker，只检查调度开关。

在开发 worktree 里起本地栈（`make dev-up`，或分开 `make dev-backend` + `make dev-worker`）时，job 一直停在 `queued` 或秒败，按顺序查这三处——`scripts/init-worktree.sh` 已尽量自动化，但各自有时机前提：

1. **Workspace 调度默认暂停**：后端每次启动都把全部 workspace 重置为暂停（刻意设计，防止重启后任务不受控自跑），unknown workspace 也默认暂停。恢复调度是按需操作：后端首次启动建表 seed 之后执行 `scripts/resume-workspaces.sh`（未建表时以退出码 1 失败并提示），或在 workspace 控制台手动恢复。症状：workflow worker 日志每 3 秒一轮但 `jobs=0`。
2. **Worker 的 models allowlist 不含任务所需模型**：agent 任务的 claim 准入按「runtime + provider/model」逐 Worker 匹配（capability 已不参与匹配，issue #284），全部 Worker 都不满足即判「无 Worker 可认领」，job 秒败并带 `not declared by any Worker` 错误。注意生效配置是状态副本 `data/agent-worker-service/worker.yaml`，首次导入后改 config 文件不生效，要走控制台或 `PUT /api/config`。
3. **`claim_enabled` 默认 false**：Worker 每次启动/手动重启都先关闭 claim（只注册心跳、不领任务），症状是后端日志没有任何 `POST /api/agent-executions/claim`。经 worker 控制台或 `PUT /api/config`（`{"claim_enabled": true}`，热字段立即生效）打开。schema v87 起 Worker 随每次状态同步（`POST /api/agent-workers/self/presence`）上报该开关，主控制台的 Worker 行会直接标成「在线·未领取」并带「控制台」入口；任务列表有「等待中」任务而无 Worker 领取时顶部还会出排查横幅。旧版 Worker 不上报（`claim_enabled: null`），仍显示为普通「在线」。例外（#681）：executor 进程崩溃（如被 OOM killer SIGKILL）后由 supervisor 自动重启时，保留操作员已打开的 claim，新进程按 `ramp_up` 重新爬坡——否则一次崩溃就会让唯一在线的 Worker 无人值守空转；但崩溃循环（上一个 executor 运行不足 60 秒）或 1 小时内已这样保留过 3 次时仍回落为关闭，控制台日志会写明原因。

领取状态属于当前注册凭据：重注册生成新 token 时清回 `null`，旧 token 的在途 presence/claim 请求不能改变新注册的状态或在线时间。presence 与 claim 在写事务中锁定当前 Worker 行并复核凭据，状态未变化时也必须完成这一步。

引导只在空态可见时请求准备状态；等待任务的排查横幅按需请求。首次未返回或刷新失败都视为未知，不据此宣称已暂停、无 Worker 或可执行；正常后台刷新保留上次成功快照。排查入口优先使用当前在线且未撤销的 Worker 地址，暂停／恢复失败会明确提示并允许重试。

## 6. 验证两个 Worker

可以直接查看 Worker 控制台，也可以在部署机执行（`GET /api/agent-workers` 要求登录用户会话，需携带登录 cookie 与 CSRF header，否则会返回 401；也可以直接在 Host Web UI 中查看）：

```bash
curl -b <登录 cookie> -H 'X-CSRF-Token: <csrf-token>' http://192.0.2.1:8000/api/agent-workers
```

响应中应同时看到 `host-local-1` 和 `remote-worker-1`。每个 Worker 还带 `allowed_workspaces`：为空表示不受 workspace 过滤的 legacy 注册——Worker 侧展示为「全部」，Host 管理 UI 展示为「待迁移（旧全局注册）」；scoped token 注册的并集永远非空。否则列出当前存活 scoped token 授权的 workspace，该字段按注册时解析的 key 绑定实时重派生——全局 token 注册已随 issue #35 退役（见 §4「token 即 scope」）。提交工作流后，Job 详情会分别显示逻辑 `agent_id` 和实际承接任务的 `worker_id`。

并发只受两层约束：每个 workspace 的 Agent 并发上限，以及各 Worker 本机的 `max_concurrency`。workspace 级上限在 workspace 设置页的「Agent 并发上限」配置（随主保存按钮一起保存），对该 workspace 的全部 Agent 节点统一生效——不再按节点单独设置。例如上限 20、三个 Worker 各 10 时，该 workspace 最多并行 20 个 Agent 执行，不要求三个 Worker 都跑满。Worker 只能 claim 其 `allowed_workspaces` 范围内 workspace 的任务。控制台修改 `max_concurrency` 会热生效，无需重启；调低容量不会终止在途任务，而是在运行数降到新上限以下前停止继续 claim。关闭「任务领取」同样只阻止新 claim，不影响已经领取的任务。code 节点任务是独立的第二个池：只受 Worker 本机 `max_code_concurrency` 约束（不占 workspace 级 Agent 上限），同样热更新免重启（0→>0 需本机已装 velites，见 §5「code 节点执行池」）。

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

# 对象存储 public endpoint（Tailnet 地址，即 AGENT_LEGION_S3_PUBLIC_ENDPOINT；TCP 连通即可）
docker compose -f deploy/compose.worker.yaml exec worker \
  python3 -c "import socket; socket.create_connection(('192.0.2.1', 8333), timeout=5); print('ok')"
```

第三条不可省略：远程 Worker 的材料与 bundle 成员走 presigned GET（`worker/material_fetch.py`、`worker/bundle_fetch.py`），产物回传走 presigned PUT staging（`worker/artifact/upload.py`），全部指向 `AGENT_LEGION_S3_PUBLIC_ENDPOINT`；compose 内部地址 `seaweedfs:8333`（默认后端；rustfs 逃生舱为 `rustfs:9000`）从远程不可达。注意内置对象存储默认只发布在 `127.0.0.1`（`deploy/compose.host.yaml` 的 `${AGENT_LEGION_S3_BIND:-127.0.0.1}` 端口映射）：远程 Worker 场景必须同时在 `deploy/.env` 设 `AGENT_LEGION_S3_BIND=<部署机 Tailnet IP>` 并把 `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 指向同一地址（presigned URL 按该地址签发），否则本条探测必然失败。若改用 HTTP 探测，根路径返回 4xx 也算可达（S3 匿名 GET `/` 本就会被拒），只有连接拒绝/超时才是失败。

三条都成功后才允许承接生产任务。如果容器内无法解析或路由到 Tailnet 地址，不要把它隐式塞进业务容器——先单独设计 Tailscale sidecar，再重新验证。

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
