# Agent Legion Host 与 Worker 部署

Agent Legion 把服务拆成两个角色：Host 负责工作流、数据库与任务调度；Worker Service 负责本机配置、状态查询和运行 Agent。即使同一台部署机同时承担两个角色，也运行两个独立服务。

Worker Service 宿主机发布面默认只绑定 `127.0.0.1:8787`（控制台和查询 API）；compose 网络内其它容器可以到达该端口，但除 `GET /api/health` 外所有端点都要求本地 control token 鉴权。首次启动会把挂载的只读引导 YAML 导入独立的可写控制卷；此后配置一律在控制台或 API 中修改，不再编辑 YAML。注册密钥（workspace scoped token）在本机控制台或 `workerctl` 中添加；页面和 API 只接受写入、不回显明文，托管副本以 `0600` 权限保存在控制卷中。

本文是部署操作的权威出处：Docker 部署机入口、Worker 注册 token、claim 默认关闭、code 节点执行池与 velites 二进制来源都以本文为准。协议版本与跨机网络见 [remote-execution-runbook.md](remote-execution-runbook.md)，对象存储 endpoint 与端口发布见 [materials-storage-deployment.md](materials-storage-deployment.md)。本文只收操作步骤；Worker 准入语义、velites 安置与升级设计（#831）、控制面鉴权判定模型、控制台入口与引导判定等设计 / 实现细节见 [architecture/agent-worker.md](architecture/agent-worker.md)。

LLM gateway 是独立基础设施，不属于 Agent Worker 协议。Worker 容器内的 velites 通过挂载的 `models.json`（provider `baseUrl` 指向 gateway）访问它。

## 1. 部署机准备密钥

在仓库根目录执行：

```bash
mkdir -p deploy/secrets
umask 077
# 只在文件不存在时生成：已部署实例重跑会覆盖 PostgreSQL 密码（数据库仍是旧
# 密码，Host 连不上）与 vault 主密钥（已存 secret 全部无法解密）。先写临时
# 文件，生成成功且非空才改名——失败不会留下空的密钥文件
gen_secret() {  # gen_secret <目标文件> <生成命令...>
  target=$1; shift
  if [ -e "$target" ]; then echo "$target 已存在，未改动" >&2; return 0; fi
  if "$@" > "$target.tmp" && [ -s "$target.tmp" ]; then mv "$target.tmp" "$target"
  else rm -f "$target.tmp"; echo "生成 $target 失败" >&2; return 1; fi
}
gen_secret deploy/secrets/postgres_password openssl rand -hex 32
gen_secret deploy/secrets/vault_master_key env UV_CACHE_DIR=.uv-cache uv run python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

把 PostgreSQL 密码写入 pgpass。以下命令中的 `<postgres-password>` 必须替换成 `deploy/secrets/postgres_password` 文件里的值：

```text
postgres:5432:agent_legion:agent_legion:<postgres-password>
```

将这一行保存为 `deploy/secrets/postgres_pgpass`，然后限制密钥文件权限：

```bash
chmod 600 deploy/secrets/postgres_password deploy/secrets/postgres_pgpass deploy/secrets/vault_master_key
```

`vault_master_key` 是实例 vault 的主密钥：必须是 32 字节密钥的 URL-safe Base64 编码（Fernet 格式），所以用上面的 `Fernet.generate_key()` 生成而不是 `openssl rand`——格式不对会在 vault 写入 / `secret_ref` 解析时报 `Vault master key is not a valid Fernet key`。compose 把它挂为 `AGENT_LEGION_VAULT_MASTER_KEY_FILE` 注入 Host（见 `deploy/compose.host.yaml`），`make prod-up docker`（`scripts/stack-prod-up.sh`）启动前对三个 secret 文件 fail-fast 检查，缺失会直接拒绝启动并指向本节。

## 2. 部署机准备挂载目录

设置 Agent skills 与 velites provider/model registry 目录。不同 runtime 各自拥有模型事实源，Worker 启动时通过 runtime adapter 动态发现。

```bash
# Host 容器把它只读挂到 /root/.agents/skills（skill root）。与 make import-demo
# 的默认落点（~/.agents/skills/...）保持一致，否则 demo skill 在容器内不可见。
export AGENT_SKILLS_DIR="$HOME/.agents/skills"
export VELITES_CONFIG_DIR="$HOME/.velites"
```

compose 还保留了 `PI_CONFIG_DIR`（→ `/root/.pi/agent`）挂载点，但 worker 镜像不含 pi 运行时（#381，见 §5「velites 二进制来源」），Docker 形态无需设置它；pi 只在裸机形态可用。

`$VELITES_CONFIG_DIR/models.json` 至少声明 provider 的 `api`（`openai-completions` 或
`anthropic-messages`）、`baseUrl`、`apiKey` 和模型列表；完整格式见
`docs/architecture/velites-model-registry.md`。建议 `apiKey` 使用 `$ENV` 引用并将文件设为
0600。容器通过 `VELITES_CONFIG_DIR` 只读挂载到 `/root/.velites`。

Docker Worker 使用独立、git-ignored 的 env file 注入这些引用变量。不要误以为 Compose
用于自身插值的 `deploy/.env` 会自动进入容器：复制示例文件，写入
`models.json` 实际引用的变量，并限制权限；也可以用绝对路径覆盖默认位置：

```bash
cp -n deploy/velites-provider.env.example deploy/velites-provider.env  # -n：已填好的文件不被示例覆盖
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

改 bind/port 的操作步骤（原生形态）：

1. 用**旧值** `make prod-down` 停掉现有实例。
2. 改根 `.env`（或临时 export）里的 bind/port。
3. `make prod-up`。
4. 绑定具体地址后，部署机本地 Worker 状态副本的 `host_url` 仍指向 loopback：在 Worker 控制台改为 `http://<绑定地址>:8000`（否则本地 Worker 静默退避重试注册、永不成功）；本机浏览器访问 Worker 控制台也改用绑定地址（`http://127.0.0.1:8787` 不再监听）。`native-prod-up.sh` 检测到这两处失配会打警告，但不代改——Worker 配置一律走控制台/API（§5）。

顺序做反时的行为：

- `native-prod-up.sh` 每启动一个服务进程就在 `data/native-prod.state` 落一份运行态记录（启动 PID 与 bind/port，#894；服务尚未监听时也已记下）。`native-prod-down.sh` 以记录为准定位实例、配置为辅：先改了配置再 down，也会按记录停掉仍在旧地址运行的实例并提示实际地址。
- 按记录 kill 前会校验该 PID 的命令行签名、`--host`/`--port` 与工作目录仍属本 worktree 的实例；实例已不在即视为记录陈旧，回落按当前配置定位；PID 仍存活却校验不过（可能是 PID 复用）时不发信号、保留记录并告警，请人工核对。记录缺失时同样回落按配置定位，此时须按旧值 down。
- prod-up 发现记录中的实例仍在另一个 bind/port 运行时拒绝启动，避免双实例；同配置重跑时若记录中的实例已启动但尚未监听（上次 up 被中断），视为已在运行并等待其就绪，超时则提示先 `make prod-down`。
- 把 bind 从具体地址切到通配（如 `127.0.0.1` → `0.0.0.0`）时同样先按旧值 down：通配监听能与同端口的具体地址监听并存，`native-prod-up.sh` 检测到通配 bind 的端口上已有其他监听即拒绝启动并列出冲突监听，避免起出连同一个库的双实例（单副本约束见 [architecture/deployment.md](architecture/deployment.md)）。

对象存储的端口发布在两种形态下都由 compose 托管（`AGENT_LEGION_S3_BIND`）：改 bind 时把 `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 指向同一可达地址；`AGENT_LEGION_S3_ENDPOINT` 保持本机/内网地址不动（Docker 形态是 compose 注入的 `http://seaweedfs:8333`，改成 LAN / Tailnet IP 会让 `local-s3-decide.sh` 判为外部存储、不再带起本地对象存储）。原生形态的 bind 取值与 endpoint 规则见 [materials-storage-deployment.md §1](materials-storage-deployment.md#1-组件与配置面)。远程 Worker 侧没有额外的网络配置项：register/claim/heartbeat/result 全部走 `host_url` 一个地址，材料、bundle 拉取与产物回传走 Host 按 `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 签发的 presigned URL——对象存储可达性由 Host 侧配置决定。

如果 LLM gateway 绑定了 Tailnet 地址并设置了 `LLM_GATEWAY_TOKEN`（绑定非 loopback 地址时必须设置），Worker 容器也需要同一个 token 才能调用 gateway。Compose 通过环境变量透传它，把 token 写进 `deploy/.env`（该文件已被 `.gitignore` 与 `.dockerignore` 排除）或导出到 shell：

```bash
umask 077
# 已有该键就替换那一行、没有才追加；读不到现有 .env 时中止，不覆盖它
# （set_env 与 materials-storage-deployment.md §3.1 的同名函数相同）
set_env() {  # set_env <文件> <键> <值>
  if [ -e "$1" ]; then
    grep -v "^$2=" "$1" > "$1.tmp"
    [ $? -le 1 ] || { rm -f "$1.tmp"; echo "读取 $1 失败，未改动" >&2; return 1; }
  else
    : > "$1.tmp"
  fi
  printf '%s=%s\n' "$2" "$3" >> "$1.tmp" && mv "$1.tmp" "$1"
}
set_env deploy/.env LLM_GATEWAY_TOKEN '<gateway-token>'
chmod 600 deploy/.env
```

不要把它写进 Compose YAML、worker.yaml 或命令行。velites 在自己的 `models.json` 中把
`apiKey` 配成 `$LLM_GATEWAY_TOKEN`（裸机 pi 同理）；Worker 把环境里的该变量透传给
agent 子进程。上游 LLM provider 凭证本身只存在于 gateway 进程。

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

Docker 部署机的唯一入口是 `make prod-up docker`（停止用 `make prod-down docker`；它目前不会停掉 profile 下的本地对象存储容器，见 §8）：

1. **放好 velites 二进制（#381）**：worker 镜像不含 agent runtime 执行器，先把与部署机架构匹配的 velites 二进制放到 `<仓库根>/velites-bin/velites`（`chmod +x`）。compose 的默认值 `${VELITES_BIN:-../velites-bin/velites}` 按 compose 文件所在的 `deploy/` 目录解析，即仓库根下的 `velites-bin/`；用 `VELITES_BIN`（绝对路径）可改位置。产物获取与架构匹配见 §5「velites 二进制来源」。
2. **准备对象存储凭据**：本地对象存储（默认 SeaweedFS）的凭据写进 `deploy/.env`，见 [materials-storage-deployment.md §3.1](materials-storage-deployment.md#31-准备凭据与配置)。
3. **启动**：

   ```bash
   make prod-up docker
   curl http://192.0.2.1:8000/api/health
   ```

   `scripts/stack-prod-up.sh` 依次经 `scripts/local-s3-decide.sh` 决策是否带起本地对象存储（开关值非法或决策启动但凭据未配齐即 fail-fast）、检查 §1 的三个 secret 文件、先起 PostgreSQL 并等它 healthy、再构建并起全 stack，最后等 host / worker 都 healthy 才返回。
4. **首次部署建 bucket**：见 [materials-storage-deployment.md §3.2](materials-storage-deployment.md#32-启动与建-bucket)。

stack 由 [compose.host.yaml](../deploy/compose.host.yaml) 编排 PostgreSQL、Host、部署机本地 Worker 与（按决策）本地对象存储。Worker 被隔离在专用 `worker-ctrl` 网络（host 双挂两个网络，postgres / 对象存储只在默认网络）：worker 控制台 `GET /` 无鉴权，网络隔离保证同 stack 的其它容器不能从 compose 内网提取其控制 token（拓扑说明见 §5「控制面鉴权」）。

`velites-bin/velites` 缺失时的实际行为：docker 会在该路径**自动创建一个空目录**并照常启动容器（long syntax bind mount 不阻止这一点，Docker Desktop 实测）；真正拦住它的是期望 runtime 守卫——compose 默认注入 `AGENT_WORKER_EXPECT_RUNTIMES=velites`，Worker 探测不到 velites 即启动预检失败（退出码 2，不自动重启，容器 unhealthy），`make prod-up docker` 因此等不到 worker healthy 而超时退出。补救时先删掉那个空目录，再放入二进制并重新 `make prod-up docker`。

`make stack-host-up` / `make stack-host-down` 是 `prod-up docker` 底层的裸 `docker compose up -d --build` / `down` 封装，只用于调试编排本身：它不检查 secret、不等待 healthy，而且 `local-s3-decide.sh` 的失败退出码被命令替换吞掉（决策失败时照常起 stack，只是不带本地对象存储）。

### 拉取式部署（v* 镜像发布，#574）

`make prod-up docker` 默认在部署机现场构建镜像（`agent-legion-host:local`）。host 镜像跟随仓库 `v*` 发版 tag 走（与 worker 的独立 `worker-v*` 版本线不同：`v*` = 仓库 + host 一体发布，`worker-v*` = 可独立重发的执行端）：push `v<数字>*` tag（如 `v0.7.8`；触发器是 char class `v[0-9]*`，刻意排除 `velites-v*` 等同前缀家族）触发 [host-image-release](../.github/workflows/host-image-release.yml) workflow——原生 runner 构建 linux/amd64 与 linux/arm64（不使用 QEMU），按 digest 合成 manifest list 后推送 GHCR `ghcr.io/luciuscao/agent-legion-host`，打 `<版本>` / `sha-<短哈希>` / `latest` 三个 tag（`sha-` 指向 tag 背后的 commit，annotated tag 亦正确 dereference）。`latest` 只在本次 tag 是远端最高版本时移动：多 tag 并发构建乱序完成、或为修复重推旧 tag 重跑，都不会把 `latest` 指回旧 manifest（此时只打版本 tag 与 `sha-<短哈希>`）。

部署机侧：复制 `deploy/compose.host.pull.example.yaml` 为 `deploy/compose.local.yaml`（`make prod-up docker` / `make prod-down docker` 与 stack-host-* 目标都自动并入），把 `image` 改成固定版本 tag，之后 `make prod-up docker` 即拉取启动（override 用 `!reset` 清除 build 段；只覆盖 host 服务，postgres / 对象存储 / worker 沿用基础文件）。GHCR 包默认 private——部署机先 `docker login ghcr.io`（具 read:packages 的 PAT），或在 GitHub package 设置中改为 public。发布走 GitHub 托管 runner，只能推 GitHub 侧 registry；需要内网私有 registry 时须自建 runner，不在本管道覆盖范围内。

与 PR 门的分工：quality-gate 的 docker-build job 只做构建验证（push: false、仅 amd64）；实际发布只由 `v*` tag 触发。注意 `v*` tag 从此有重副作用（触发镜像构建）：若 tag 已打但构建失败，修复后重推同 tag 即可（cache-to 落在 tag ref 下，重跑可复用缓存）。

## 4. Worker 机器准备

Worker 机器按以下顺序准备。部署机本地 Worker 由 §3 的 stack 一并带起，引导配置 `deploy/worker.host.example.yaml` 已指向 compose 内的 `http://host:8000`，只需第 4 步：

1. **放好仓库与 velites 二进制**：把与部署机同一版本的仓库放到 Worker 机器，并按 §3 第 1 步把架构匹配的 velites 放到 `<仓库根>/velites-bin/velites`。没有仓库的机器改用 §5「一键安装」。
2. **准备 velites 模型注册表**：同 §2，`VELITES_CONFIG_DIR` 指向含 `models.json` 的目录，gateway 设置了 token 时提供 `LLM_GATEWAY_TOKEN`。
3. **决定引导配置**：`deploy/compose.worker.yaml` 默认把 `deploy/worker.remote.example.yaml` 挂为引导 YAML，Worker **首次启动时导入它**（`worker_id: remote-worker-1`、`host_url` 为文档示例地址、`labels.arch: arm64`）。二选一：
   - 直接启动，再在控制台把 Host 地址、Worker ID、标签改成本机的值（导入后以控制台为准，#323）；
   - 或启动前复制一份修改：`cp -n deploy/worker.remote.example.yaml deploy/<my-worker>.yaml`（`-n`：重跑不覆盖已改好的文件），改好 `host_url` / `worker_id` / `labels`，再 `export AGENT_WORKER_CONFIG=./<my-worker>.yaml`——该路径按 compose 文件所在的 `deploy/` 目录解析。

   容器内运行的是 Linux，因此标签中的 `os: linux` 是有意的；`arch` 按宿主机架构填（Apple Silicon 为 `arm64`）。
4. **签发并导入 workspace 注册 token**（下文「注册 token」）：Host 侧签发，Worker 侧在控制台添加或用 `workerctl` 导入。

### 注册 token（token 即 scope）

Worker 的注册 token 决定它能进入哪些 workspace：`worker.yaml` 不需要也不允许声明 workspace，全局 register token 已退役（#35），只有 workspace scoped token 一种。

1. **签发**：Host Web UI 的 workspace「设置 → Agent 与 Worker」页面填写 Key 名称即可创建（固定绑定当前 workspace）。明文 token 只显示一次。
2. **导入 Worker**：到 Worker 控制台（`http://<worker>:8787` 配置页）的「Workspace 访问（Scoped Token）」区块粘贴添加；无显示器的设备用 `workerctl configure --register-token-file`（容器形态经 stdin 传入，见 §5「控制面鉴权」的 CLI 示例）。
3. **多 workspace**：一个 Worker 可添加多个不同 workspace 的 token，注册时全部呈现，Host 取并集作为 scope；任何一个 token 失效（已删除/未知）都会让整次注册 401。注册后 Worker 只能看到并 claim 授权 workspace 的任务；Host 侧每个 workspace 的设置页也只显示用本 workspace token 注册的 Worker（管理员仍可见全量）。
4. **切断访问**：删除 key 是唯一方式，没有单独的「吊销 Worker」操作。删除在同一事务内级联：不再持有任何存活 key 的 Worker 记录一并删除、凭证立即失效；仍持有其它 key 的 Worker 保留记录，scope 收窄到剩余 key 的范围。无绑定记录的 legacy Worker 不受级联影响，可在同一页面手动删除。旧版「已吊销（revoked）」记录不再生效（列表显示「已失效（旧版吊销）」只是遗留标记）：只要该 Worker 还持有存活 key，重新注册即恢复，要永久切断必须删除它持有的全部 key。

也可以在部署机上用 curl 调同一组管理端点（`/api/agent-register-tokens*`、`DELETE /api/agent-workers/{id}`，均要求 admin 会话）。会话 cookie 是 `agent_legion_session`；cookie 鉴权下的变更请求（POST / DELETE）还必须带 `x-agent-legion-request: 1`，缺了返回 403 `Missing request header`，不带 cookie 返回 401：

```bash
# 登录（admin 账号），把 session cookie 存进 0600 文件；在提示处粘贴
# {"username": "<admin>", "password": "<密码>"} 后按 Ctrl-D，密码不进 shell 历史
umask 077
curl -sS -c ./al-cookies.txt -H 'Content-Type: application/json' -d @- \
  http://192.0.2.1:8000/api/auth/login

# 签发（明文只返回这一次）
curl -sS -b ./al-cookies.txt -H 'x-agent-legion-request: 1' \
  -H 'Content-Type: application/json' \
  -d '{"workspace_id": "<workspace_id>", "label": "remote-worker"}' \
  http://192.0.2.1:8000/api/agent-register-tokens
# => {"token_id": "...", "register_token": "<明文，只返回这一次>", "workspace_id": "<workspace_id>", "label": "remote-worker"}

# 列表（GET 只需 cookie；不含明文与 hash）
curl -sS -b ./al-cookies.txt http://192.0.2.1:8000/api/agent-register-tokens

# 删除 key（硬删、级联，立即失效）
curl -sS -b ./al-cookies.txt -H 'x-agent-legion-request: 1' \
  -X DELETE http://192.0.2.1:8000/api/agent-register-tokens/<token_id>
```

用完先注销会话（同样带 cookie 与 `x-agent-legion-request: 1` 调 `POST /api/auth/logout`），再删除 `al-cookies.txt`。Worker 注册本身凭 scoped token，不经这组管理端点。

## 5. 启动 Worker 机器上的 Worker

1. 启动并看日志：

   ```bash
   make stack-worker-up
   make stack-logs STACK=worker
   ```

2. 打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)。默认回环发布（`AGENT_WORKER_UI_BIND` 未设或为回环地址）下 worker ≥ 0.7.16 的页面自动内嵌 control token，打开即用；发布到非回环地址时需手动输入一次控制令牌（取法与判定矩阵见下文「控制面鉴权」）。直接用浏览器打开 `worker/ui/index.html` 静态文件不可用。
3. 确认 Host 地址（部署机可经 Tailscale 访问的地址）、Worker ID 与标签，保存；添加 §4 签发的注册 token。
4. 确认已注册后点击「开始领取」（或 `workerctl claim enable`）——claim 默认关闭，见下文。

页面可以看到：

- Worker 执行进程是否运行；
- 当前配置的 Host 地址以及 Host 是否可达；
- 当前 `worker_id` 是否已在该 Host 登记、最后在线时间；
- 是否允许主动 claim 新任务，以及当前运行数 / 动态容量；
- 注册令牌允许接入的 Workspace 范围；
- 运行时、并发数、标签和最近日志。

页面保存配置后会原子写入控制卷。身份、可用模型或注册 Token 变化时会重启执行进程并重新注册；领取开关和热字段（`max_concurrency` / `max_code_concurrency` / `upload_max_concurrency` / `ramp_up` / `claim_batch_limit`）都会热更新，无需重启。

**claim 默认关闭**（本节是该规则的权威出处）：Worker 执行进程每次启动（服务启动、手动重启）都先把 `claim_enabled` 置为 false，即使上次退出前是开启状态；必须在控制台点击「开始领取」或执行 `workerctl claim enable` / `PUT /api/config {"claim_enabled": true}`，Worker 才会按本机容量拉取任务。唯一例外是执行进程崩溃（如被 OOM killer 杀掉）后由 supervisor 自动重启（#681）：保留操作员已打开的 claim，新进程按 `ramp_up` 重新爬坡；但上一个执行进程运行不足 60 秒（崩溃循环），或 1 小时内已这样保留过 3 次时，仍回落为关闭，控制台日志写明原因（`worker/restart_policy.py`）。

Worker 不需要声明 `capabilities`；runtime 由本机二进制探测自动启用（`disabled_runtimes` 反选），`models` 是可选的 allowlist。agent 任务准入条件（token 授权、runtime、provider/model allowlist、`requires_labels`）与发现语义见 [architecture/agent-worker.md §1](architecture/agent-worker.md#1-runtime-声明与-agent-任务准入)。

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
curl -fsSL https://raw.githubusercontent.com/LuciusCao/agent-legion/main/scripts/install-worker.sh \
  | sh -s -- --host-url http://<部署机IP>:8000 --worker-id my-worker-1 --version <worker 版本>
```

**版本**：脚本的默认 worker 版本（`AGENT_WORKER_VERSION` 默认值）由每次发版的落版 commit 钉为当次发布的版本，所以只有 `main` 上的脚本默认值等于最新正式发布；其他分支上的副本可能落后若干版本。部署时显式传 `--version`，取值与部署机 Host 版本配套（须存在对应的 `worker-v<版本>` tag，见 GitHub Releases）。

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

- **Docker 部署**：从 GitHub Release（`velites-v*` tag，velites-release workflow 产出）下载与宿主机架构一致的 tarball，解出的 `velites` 放到 compose 的 `VELITES_BIN`（默认 `../velites-bin/velites`，按 `deploy/` 解析，即 `<仓库根>/velites-bin/velites`；一键安装形态为安装目录下的 `velites-bin/velites`）——compose 把它 bind mount 到容器内 `/app/data/bin/velites`（自带副本目录，优先于 PATH）。源文件缺失时 docker 会在该路径自动建空目录并照常启动容器，由下述期望 runtime 守卫让 Worker 启动失败（§3）。架构必须与 worker 镜像一致（x86_64 取 `*-x86_64-unknown-linux-gnu`，arm64 取 `*-aarch64-unknown-linux-gnu`）；挂载了错误架构的二进制能通过存在性探测，但执行时以 exec format error 失败——期望 runtime 守卫会把它转成启动失败（见下）。**防漏挂载守卫**：compose 默认注入 `AGENT_WORKER_EXPECT_RUNTIMES=velites`（`deploy/.env` 可覆盖：多值逗号分隔；显式置空禁用守卫，零 runtime 注册合法——零 runtime / 纯 code 池形态同时叠加 `deploy/compose.worker.zero-runtime.yaml` override 去掉 velites bind mount，否则无条件挂载会在 `velites-bin/` 下留一个无用的空目录），启动时探测不到期望 runtime、或期望 runtime 模型发现失败（含架构错配）即 fail-fast（退出码 2，supervisor 不自动重启、healthcheck 变 unhealthy）。**pi 在 docker 镜像内不可用**：pi 的入口是 npm 包脚本，依赖 node 运行时与包树，而 #381 已把它们移出镜像——pi 部署走裸机形态（PATH 或 `data/bin/`），需要在 docker 跑 pi 时自行构建含 node+pi 的镜像变体；
- **裸机/开发部署**（直接跑 `worker.executor`，如 `make dev-worker`）：在**与 Worker 同 OS/架构**的机器上、仓库根执行 `./scripts/ensure-velites.sh --dest data/bin`，脚本按 velites/ 源码指纹决定是否需要 `cargo build --release`（指纹不变的重复执行直接跳过），产物原子安置到 `data/bin/velites`。无源码/工具链的纯执行节点可直接取 Release 产物安置到同一目录（此时不要跑 `make prod-up`——见下条升级语义）。**注意不要给 Release 产物手写 `.src-stamp`**：产物不带 stamp、Release 也不发布其构建 tree hash，而 velites 与仓库版本线解耦——产物源码往往旧于本 checkout，手写当前 HEAD 指纹（`git rev-parse HEAD:velites > data/bin/velites.src-stamp`）会给旧二进制伪造新鲜度，使 prod-up 永久跳过重建、启动对账（staleness）不再报漂移，比 #831 更彻底地静默。无 cargo 但需要刷新 `data/bin` 的合法出路只有两条：装 Rust 工具链重建，或在与本机同 OS/架构、同一仓库状态的机器上跑 `ensure-velites.sh --dest data/bin` 后**把二进制与 stamp 一起拷贝**（stamp 与产物同源才可信）。macOS 产物用 seatbelt、Linux 产物用 bubblewrap（Linux 主机需可用的 bwrap：setuid 或非特权 user namespace），沙箱后端不可用同样 fail-closed。裸机部署同样可设 `AGENT_WORKER_EXPECT_RUNTIMES`（如 systemd 单元的 `Environment=`）启用期望 runtime 守卫；不设时保持「探测到什么声明什么」的默认语义。
- **升级（#831）**：原生形态 `make prod-up` 对 velites 的两个安置点（PATH 与 `data/bin` 自带副本）**都**按源码指纹刷新，沙箱包装器 `velites-sandbox` 同批刷新；重建需要 cargo，新 worktree 首次 prod-up 无 cargo 即 fail-fast（合法出路见上一条）。`data/bin` 副本与源码漂移时 Host / Worker 启动日志打 WARNING（软告警，不 fail-closed）。planner 推导、家族级判鲜与启动对账的设计见 [architecture/agent-worker.md §3](architecture/agent-worker.md#3-velites-安置点与升级语义831)。

  运行时监督的灰度/回退开关（同 family 的 stdout 事件泵开关 `AGENT_WORKER_EVENT_PUMP` 一并适用）：`AGENT_WORKER_EXIT_WATCH=kqueue|pidfd|scan|auto`（默认 auto：macOS 选 kqueue、Linux 选 pidfd、都不可用回落 scan——单根 watcher 线程监听全部在飞子进程的退出；显式指定可用即遵守、不可用回落 scan 并打印原因）；`AGENT_WORKER_LANE_IDLE_TIMEOUT=<秒>`（默认 30，执行车道线程的空闲退出时限）。两者只在出现监督面异常时需要动——slots 心跳行会持续打印 `exit <mode>` 与 `lane <n>` 供核对。

**secret 边界**：节点 secret 只经 claim 响应的 HTTPS 通道下发、Worker 仅内存持有，不落盘、不进日志，部署侧无需额外配置；实现见 [architecture/agent-worker.md §2](architecture/agent-worker.md#2-code-任务的-secret-边界)。

**协议兼容与升级顺序**：当前协议版本、各版本能力、混合舰队兼容矩阵、结果上报头对反向代理的要求与 **Host first, Worker second** 升级纪律的权威出处是 [remote-execution-runbook.md §5](remote-execution-runbook.md#5-workers)。镜像形态同样适用：先升级部署机 Host 并确认健康，再逐台 pull 新 worker 镜像重启；回滚时 Host 与 Worker 一起退。

节点的 provider、model、thinking 和 prompt 可以继续在 workflow 编辑器中修改。只修改这些运行配置会更新当前 revision，而不会创建新版本；已创建但尚未领取的 Job 会在领取时使用其 revision 的最新运行配置。任务一旦领取，就固定使用领取时下发的配置。

### 控制面鉴权

控制令牌（登录 Worker 控制台）与 workspace 注册 Key（授权 Worker 接入 Host）用途不同。页面没有内嵌控制令牌时（非回环发布或 worker < 0.7.16，判定见下文），部署机 Host Compose 在项目根目录运行以下命令取得控制令牌，粘贴到 Worker 登录框后，再到「配置 → Workspace 访问」添加注册 Key：

```bash
docker compose -f deploy/compose.host.yaml exec -T worker cat /var/lib/agent-legion-worker-control/control_token
```

独立 Worker 部署将 Compose 文件换成启动时使用的 `deploy/compose.worker.standalone.yaml` 或 `deploy/compose.worker.yaml`，保留相同项目名及其他 Compose 参数。原生部署从 Worker 的 `--state-dir` 目录读取 `control_token`；没有该机器访问权限时由 Worker 维护者完成登录。控制令牌不要放进控制台 URL、Host 配置或注册标签。

Worker Service 启动时在状态卷生成（或复用）`/var/lib/agent-legion-worker-control/control_token`（权限 0600）。除 `GET /api/health` 外，所有 `/api/*` 端点都要求 `Authorization: Bearer <token>`。`workerctl` 按以下顺序取 token：`--token` 参数 > `AGENT_WORKER_CONTROL_TOKEN` 环境变量 > `--state-dir` 目录下的 `control_token` 文件。`--state-dir` 的默认值是相对当前目录的 `data/agent-worker-service`（裸机/dev 布局），**容器内不会自动命中**：镜像的工作目录是 `/app`，状态卷挂在 `/var/lib/agent-legion-worker-control`，所以容器内调用必须显式传 `--state-dir /var/lib/agent-legion-worker-control`（全局参数，写在子命令之前），否则报「读不到控制令牌（data/agent-worker-service/control_token）」。

**页面何时内嵌 token**（判定矩阵、Host 头校验与 `compose.host.yaml` 的 `worker-ctrl` 网络隔离设计见 [architecture/agent-worker.md §4](architecture/agent-worker.md#4-控制面鉴权的判定模型)）：宿主侧发布地址 `AGENT_WORKER_UI_BIND` 为回环（默认）时内嵌、打开即用；发布到非回环地址，或 `AGENT_WORKER_CONSOLE_URL` 含非回环主机名时不内嵌，需手动输入一次 token。经主机名（反向代理、MagicDNS 名等）访问控制台时，必须把该地址写进 `AGENT_WORKER_CONSOLE_URL`，否则 Host 头校验返回 403。内嵌机制随 **worker 0.7.16** 发布，更旧版本始终需手动输入。

运维注意：

- **compose override 改发布地址时必须同步设置 `AGENT_WORKER_UI_BIND`**：compose 合并 override 时 `ports` 列表整体替换、`environment` 按 key 合并——只在 override 里改 `ports` 发布地址（或加新条目）而不同步 `.env` 的 `AGENT_WORKER_UI_BIND`，两个插值源就会漂移，service 会按旧的 EFFECTIVE_BIND 判定内嵌（页面实际已发布到非回环地址 = 泄漏）或反向多要一次手动 token。仓库内两个 worker compose 的 `ports` 行与 `EFFECTIVE_BIND` 同用 `${AGENT_WORKER_UI_BIND}` 插值，`.env` 一处改两处同步就是为此。
- **`compose.host.yaml` 形态的 `AGENT_LEGION_S3_PUBLIC_ENDPOINT` 必须用宿主侧发布地址**：worker 被隔离在 `worker-ctrl` 网络，覆盖为 compose 服务名（如 `http://seaweedfs:8333`）会不可达——产物直传回落经 host 的 CAS 通道，材料任务会失败。

发布非回环时，从容器内手动取一次 token，页面会把它存进 localStorage，日常无需重复：

```bash
# 一键安装目录内（compose 文件为 docker-compose.yaml）；仓库形态加 -f deploy/compose.worker.yaml
docker compose exec -T worker cat /var/lib/agent-legion-worker-control/control_token
```

#### 容器内 CLI（workerctl）

终端查询或自动化用容器内的 `workerctl`。先定义一个 shell 函数，把 compose 文件与 `--state-dir` 固定下来（部署机本地 Worker 换成 `-f deploy/compose.host.yaml`，一键安装目录内去掉 `-f`）：

```bash
wctl() {
  docker compose -f deploy/compose.worker.yaml exec -T worker \
    workerctl --state-dir /var/lib/agent-legion-worker-control "$@"
}

wctl status
wctl claim status
wctl claim enable
wctl claim disable
wctl capacity
wctl capacity 8
wctl config
wctl logs --limit 100
wctl --json logs --limit 100
wctl restart
```

`claim enable/disable` 和 `capacity <数量>` 都是热更新，不会重启执行进程，也不会中断已领取任务；新的容量会在下一次 claim 时同步到 Host 并即时生效（无需重新注册）。所有查询命令均可配合全局 `--json` 输出机器可解析的 JSON；读操作超时 5 秒，`configure`/`restart` 等变更操作超时 60 秒（服务端停止预算约 25 秒）。

也可以直接访问仅限本机的查询接口（先取出 token）：

```bash
TOKEN=$(docker compose -f deploy/compose.worker.yaml exec -T worker cat /var/lib/agent-legion-worker-control/control_token)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/status
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/api/config
curl -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8787/api/logs?limit=100'
```

CLI 修改配置的示例（`configure` 是部分更新：只覆盖显式传入的字段，未指定的字段保持现状，`--host-url`/`--worker-id` 均非必填；但列表字段整体替换——传了 `--model` 就以本次全部 `--model` 取代已声明的模型列表，`--label` 同理取代全部标签，改其中一项时要把想保留的项一并传入，先用 `wctl config` 读出现状）：

```bash
wctl configure \
  --host-url http://192.0.2.1:8000 \
  --worker-id remote-worker-1 \
  --name 'Remote Worker' \
  --max-concurrency 10 \
  --model openai/gpt-5.2 \
  --label os=linux --label arch=arm64
```

**导入注册 token**：`--register-token-file` 读的是 `workerctl` 进程所在文件系统上的路径——容器形态下就是**容器内**路径，宿主机上的 `./marketing.token` 在容器里不存在（报「无法读取注册 token 文件」）。把宿主机上的 token 文件经 stdin 传进去（`exec -T` 已在 `wctl` 里）：

```bash
# marketing.token：从 Host Web UI 复制的明文，宿主机上以 0600 保存（文件名自定）
wctl configure --register-token-file /dev/stdin < ./marketing.token
```

`workerctl` 把读到的 token 经 loopback 控制 API 写入状态卷里 0600 的 `register_tokens/`（与控制台「添加并验证」同构），之后即可删除宿主机上的 token 文件；不要把 token 明文放进命令参数或 shell 历史。重复执行可添加多个 workspace 的 token。也可以先 `docker compose cp ./marketing.token worker:/tmp/` 再传容器内路径，但那样 token 副本会留在容器可写层里，需自行删除，不推荐。裸机形态直接传本机路径即可。

`--disable-runtime <runtime>` 用于反选停用本机已安装的 runtime，只在该 runtime 不属于期望集合时可用：Docker 形态 compose 默认注入 `AGENT_WORKER_EXPECT_RUNTIMES=velites`，此时 `--disable-runtime velites` 会让启动预检以「期望 runtime 与 disabled_runtimes 冲突」失败（退出码 2，进入 failed）。它的典型场景是裸机上同时装了 pi 与 velites、只想接其中一个；确需停用期望 runtime 时先改 `AGENT_WORKER_EXPECT_RUNTIMES`。

对于树莓派、云服务器等无显示器设备，上述 `workerctl` 命令覆盖初始化、状态检查、模型声明、动态扩容、claim 开关、日志与进程重启，不依赖浏览器。

### 崩溃重启与失败状态

Host 暂时不可达或返回 5xx 时，执行进程会保持运行并在进程内指数退避重试注册，不会打印 traceback，也不会触发 supervisor 重启。执行进程因其他原因崩溃后按指数退避自动重启：5 秒起步、每次 ×2、封顶 300 秒；稳定运行满 60 秒后重置退避。退出码 2 不自动重启，进入 failed 状态，需修正后手动 `workerctl restart`。退出码 2 的来源：

- Host 明确拒绝注册（注册请求返回 400 / 401 / 403 / 409 / 422），例如持有的全部 key 已被删除。
- Worker 自行拒绝 Host：Host 注册成功（201），但响应里的 `host_protocol_version` 低于 Worker 自身协议版本（旧 Host 缺该字段按 0 处理），Worker 收到后以退出码 2 拒绝继续，不进入 claim（升级顺序与兼容矩阵见 [remote-execution-runbook.md §5](remote-execution-runbook.md#5-workers)）。
- 启动预检失败：`AGENT_WORKER_EXPECT_RUNTIMES` 声明的 runtime 探测不到（Docker 形态最常见的是 `velites-bin/velites` 没放好，§3）、该 runtime 模型发现失败（含二进制架构错配）、或它被 `disabled_runtimes` 停用；`max_code_concurrency > 0` 但沙箱包装器（velites-sandbox 或 velites）在自带副本目录与 PATH 上都找不到（docker 形态该包装器内置镜像，此错误通常意味着镜像损坏）。

`status` 中的 `restart_count`、`next_restart_delay`、`failed` 字段反映这些状态；容器 healthcheck 会把 failed 或已配置但进程未运行视为 unhealthy。

### 挂载配置与状态副本不一致

首次启动把 `--config` 挂载的 YAML 导入状态卷后，再修改挂载文件不会自动生效。`status` 的 `mounted_config_diverged` 为 true 表示两者内容已分叉，处理路径二选一：

- 用 `workerctl configure`（或控制台）把新值写入状态副本并重启执行进程；
- 或删除状态卷中的 `worker.yaml` 后重启容器，重新导入挂载配置。

默认端口只绑定宿主机 loopback；compose 网络内其它容器可达 `http://worker:8787`，但所有端点（除 `/api/health`）都要求 control token；`compose.host.yaml` 形态下同 stack 的其它服务更被网络隔离挡在控制台之外（网络拓扑见 [architecture/agent-worker.md §4](architecture/agent-worker.md#4-控制面鉴权的判定模型)），只有 host 容器可达。需要从 Tailnet 上的另一台管理机访问时，显式设置 `AGENT_WORKER_UI_BIND`，并先在主机防火墙或 Tailnet ACL 中限制来源；不要把控制面暴露到公网。非回环发布下控制台不再内嵌 token（判定矩阵与取 token 命令见 §「控制面鉴权」）。

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
   workspace 签发 scoped token；到 Worker 控制台（dev 默认 `http://127.0.0.1:8789`）的
   「Workspace 访问（Scoped Token）」区块粘贴添加。Worker 侧 token 随时可以
   补——注册失败只影响 Worker 自身，不需要重启后端。
3. 重跑 `make dev-up`（幂等）启动 Worker，然后在 worker 控制台打开
   `claim_enabled`（默认关闭，见上文「claim 默认关闭」）。

### 「打开 Worker 控制台」入口

Host 设置页顶部的「Worker 与 Worker 控制台」卡片、签发成功后的「下一步」以及各处 Worker 列表空态都带「打开 Worker 控制台」入口；主控制台每一行 Worker 还可以带该 Worker 自报的「控制台」链接。

- **部署级入口**：地址来自后端 env `AGENT_LEGION_WORKER_CONSOLE_URL`。`make dev-up` 按 Worker 端口、`native-prod-up.sh` / Host compose 按 `:8787` 注入默认值（dev/native 脚本只设置内部的 `AGENT_LEGION_WORKER_CONSOLE_DEFAULT_URL`，后端按「进程环境 → 根 `.env` → 脚本默认值」选择，显式空值始终有效）。Worker 控制台经其它地址暴露时在 `.env` 显式配置；显式留空则不显示链接。回环地址只能在 Worker 所在机器的浏览器里打开，链接的悬停提示会说明这一点。
- **Worker 自报入口**：原生 Worker Service 按自己的控制面绑定地址推导（通配 `0.0.0.0` 回落 `127.0.0.1`，IPv6 `::` 回落 `[::1]`），经 `AGENT_WORKER_CONSOLE_URL` 交给执行进程，注册时作为可选标签 `console_url` 上报（`worker/console_url.py`）。三份 Compose 都要求在部署环境中显式配置浏览器可达 URL（如 `https://worker.example/console`），不从 `AGENT_WORKER_UI_BIND` 猜测，缺省或显式空串均不自报；旧版 Worker 不上报，对应行只保留部署级入口。
- **地址要求**：非空配置必须是绝对 HTTP(S) 地址（支持 IPv6、反向代理路径与 query，不得含 URL 用户名/密码），非法值在服务创建前报错；控制令牌不放进该地址。标签保留、校验细节与前端获取行为见 [architecture/agent-worker.md §5](architecture/agent-worker.md#5-打开-worker-控制台入口的实现约束)。

### 开发 worktree 的本地 Worker 检查单

在开发 worktree 里起本地栈（`make dev-up`，或分开 `make dev-backend` + `make dev-worker`）时，job 一直停在 `queued` 或秒败，按顺序查这三处——`scripts/init-worktree.sh` 已尽量自动化，但各自有时机前提：

1. **Workspace 调度默认暂停**：后端每次启动都把全部 workspace 重置为暂停（刻意设计，防止重启后任务不受控自跑），unknown workspace 也默认暂停。恢复调度是按需操作：后端首次启动建表 seed 之后执行 `scripts/resume-workspaces.sh`（未建表时以退出码 1 失败并提示），或在 workspace 控制台手动恢复。症状：workflow worker 日志每 3 秒一轮但 `jobs=0`。
2. **Worker 的 models allowlist 不含任务所需模型**：agent 任务的 claim 准入按「runtime + provider/model」逐 Worker 匹配（capability 已不参与匹配，issue #284），全部 Worker 都不满足即判「无 Worker 可认领」，job 秒败并带 `not declared by any Worker` 错误。注意生效配置是状态副本 `data/agent-worker-service/worker.yaml`，首次导入后改 config 文件不生效，要走控制台或 `PUT /api/config`。
3. **`claim_enabled` 默认 false**（规则见 §5「claim 默认关闭」）：只注册心跳、不领任务，症状是后端日志没有任何 `POST /api/agent-executions/claim`。经 worker 控制台或 `PUT /api/config`（`{"claim_enabled": true}`，热字段立即生效）打开。Worker 随每次状态同步（`POST /api/agent-workers/self/presence`）上报该开关，主控制台的 Worker 行会直接标成「在线·未领取」并带「控制台」入口；任务列表有「等待中」任务而无 Worker 领取时顶部还会出排查横幅。旧版 Worker 不上报（`claim_enabled: null`），仍显示为普通「在线」。崩溃自动重启的例外见 §5「claim 默认关闭」。

新 workspace 引导、排查横幅与「在线·未领取」状态的判定规则（未知状态不推断、领取状态绑定当前注册凭据）见 [architecture/agent-worker.md §6](architecture/agent-worker.md#6-领取状态与引导--排查横幅的判定)。

## 6. 验证两个 Worker

可以直接查看 Worker 控制台或 Host Web UI，也可以在部署机按 §4 的登录命令重新登录得到 cookie 文件后查询（用完同样注销并删除）（`GET /api/agent-workers` 要求登录用户会话；只读 GET 不需要 `x-agent-legion-request` 头，不带 cookie 返回 401）：

```bash
curl -sS -b ./al-cookies.txt http://192.0.2.1:8000/api/agent-workers
```

响应中应同时看到 `host-local-1` 和 `remote-worker-1`。每个 Worker 还带 `allowed_workspaces`：为空表示不受 workspace 过滤的 legacy 注册——Worker 侧展示为「全部」，Host 管理 UI 展示为「待迁移（旧全局注册）」；scoped token 注册的并集永远非空。否则列出当前存活 scoped token 授权的 workspace，该字段按注册时解析的 key 绑定实时重派生——全局 token 注册已随 issue #35 退役（见 §4「注册 token」）。提交工作流后，Job 详情会分别显示逻辑 `agent_id` 和实际承接任务的 `worker_id`。

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

第三条不可省略：远程 Worker 的材料与 bundle 成员走 presigned GET（`worker/material_fetch.py`、`worker/bundle_fetch.py`），产物回传走 presigned PUT staging（`worker/artifact/upload.py`），全部指向 `AGENT_LEGION_S3_PUBLIC_ENDPOINT`；compose 内部地址（如 `seaweedfs:8333`）从远程不可达。内置对象存储默认只发布在 `127.0.0.1`，远程 Worker 场景要同时改 `AGENT_LEGION_S3_BIND` 与 `AGENT_LEGION_S3_PUBLIC_ENDPOINT`，否则本条探测必然失败——配置规则见 [materials-storage-deployment.md §1](materials-storage-deployment.md#1-组件与配置面)。若改用 HTTP 探测，根路径返回 4xx 也算可达（S3 匿名 GET `/` 本就会被拒），只有连接拒绝/超时才是失败。

三条都成功后才允许承接生产任务。如果容器内无法解析或路由到 Tailnet 地址，不要把它隐式塞进业务容器——先单独设计 Tailscale sidecar，再重新验证。

## 8. 停止服务

部署机：

```bash
make prod-down docker
```

注意：`make prod-down docker`（以及 `make stack-host-down` / `make stack-down`）执行的是不带 `--profile` 的 `docker compose ... down`，**不会停掉** profile 下的本地对象存储容器（`seaweedfs` 在 `materials-local`、`rustfs` 在 `materials-local-rustfs`），这些容器继续运行（compose v5 实测）。要连同对象存储一起停，显式带上对应 profile（存在 `deploy/compose.local.yaml` 时同样加 `-f`）：

```bash
docker compose -f deploy/compose.host.yaml --profile materials-local down
# rustfs 逃生舱：--profile materials-local-rustfs
```

Worker 机器：

```bash
make stack-worker-down
```

也可以在任何一台机器上用 `make stack-down` 同时停止两个 stack，用 `make stack-status STACK=host|worker` 查看容器与健康状态。停止命令不会删除命名卷中的 PostgreSQL 或运行数据。
