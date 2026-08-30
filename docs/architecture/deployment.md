# 部署与配置

## Overview

Agent Legion 使用 PostgreSQL 作为唯一控制面数据库；开发机和生产环境使用同一数据库语义。

## Directory Structure

```
config/
├── agent-worker.example.yaml # Agent Worker 配置模板
└── architecture/             # 架构治理配置（不变量、豁免、体积预算）

# 运行时 split 配置（app.yaml / workflow.yaml / agent_legion.yaml）已整体退役：
# 代码默认值 + env 覆盖 + DB 实例设置文档，文件存在即启动报错（带迁移指引）。
# skill 源与锁（skills.yaml / skills.lock）亦已退役：声明与解析后的 commit 锁
# 存 DB global_settings（skill_sources / skill_lock），经 /admin/settings
# 「Skill 源管理」或 make skills-lock 管理；残留文件启动时一次性导入 DB
# （warning），此后不再读取。

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

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### 顶层配置项

_全部运行时配置段已从 split yaml 退役：业务参数在 capability config_schema（Studio 节点/workspace 配置覆盖），实例级调参在 DB 实例设置文档（/api/admin/instance-settings），机器路径与密钥走 env（如 AGENT_LEGION_DATABASE_URL）。_

<!-- END AUTO-GENERATED -->
