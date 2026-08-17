# Agent Legion — Agent Operating Manual

本文件只包含 AI Agent 在修改本仓库时必须遵守的纪律与红线。
项目概览、安装、运行命令见 [README.md](README.md)；架构与实现细节见 [docs/architecture/](docs/architecture/)。

## 1. Worktree & Isolation

- 每次独立开发任务优先在新的 git worktree 中进行。
- worktree 一律建为主仓库根的平级子目录：先 `cd` 到主仓库根（`git worktree list` 的第一个条目），再 `git worktree add .worktrees/<name> -b <branch> <base>`。禁止嵌套（在其他 worktree 里用相对路径 `git worktree add .worktrees/<name>` 会建进当前 worktree 内部）——嵌套会让 `data/`、测试库派生、端口隔离和清理路径全部混乱。
- 不同 worktree 使用不同 backend/frontend 端口与独立 `data/` 目录，避免数据库、视频、日志、package 互相覆盖。
- 创建新 worktree 后，从基准 worktree 复制 `.env` 到新的 worktree 根目录，确保测试、后端服务与外部集成配置一致。
- 推荐用 `scripts/init-worktree.sh` 一键完成初始化（幂等）：复制 `.env`、按 worktree 名派生并创建专属 Postgres 库、生成缺失的 `deploy/secrets/agent_worker_register_token` 与 `deploy/secrets/vault_master_key`、缺失时从基准 worktree 种子 `config/agent-worker.yaml`（改写 host_url/worker_id）、恢复 workspace 调度。手工初始化时必须自行补上这两个 secrets 文件。两个 secret 缺失的后果不同：register token 若被 `register_token_file` 显式引用而文件缺失，启动 fail fast（未显式配置时启动只尝试读 repo 默认路径 `deploy/secrets/agent_worker_register_token`，存在才生效）；vault master key 是 env-only（`AGENT_LEGION_VAULT_MASTER_KEY` / `_FILE`），代码没有默认读文件路径，缺 key 服务照常启动，但 vault 写入与 `secret_ref` 解析会抛 `VaultMasterKeyMissingError`。
- 开发实例两个默认关闭的开关（刻意设计，防失控自跑）：后端每次启动把全部 workspace 重置为暂停，需恢复调度（init 脚本已做，但**必须在后端首次启动建表之后**执行才生效，否则启动后重跑一次脚本）；worker 的 `claim_enabled` 默认 false，启动后经 worker 控制台或 `PUT /api/config` 打开。worker 生效配置是状态副本 `data/agent-worker-service/worker.yaml`，首次导入后改 `config/agent-worker.yaml` 不生效。
- 新 worktree 必须配置独立 Postgres 数据库：在 `.env` 中加 `AGENT_LEGION_DATABASE_URL` 指向专属库（`database.url` 为 env-only，代码默认库是共享库，不要依赖默认值）。共享库会让任一 worktree 的进程启动（含质量门里的 `export_openapi`）清掉其他实例的 `worker_control_state` 等运行时状态。
- 测试库无需手动配置：`tests/postgres_support.py` 按 worktree 目录名派生专属测试库（`agent_legion_test_<worktree>`）并在首次测试运行时自动建库；只有需要覆盖时才设 `AGENT_LEGION_TEST_DATABASE_URL`。
- 测试并行度默认克制：后端 pytest-xdist 默认 min(4, 核数)（`AGENT_LEGION_TEST_WORKERS` 覆盖），前端 vitest 经 gate 脚本默认 `--maxWorkers=4`（`AGENT_LEGION_FRONTEND_TEST_WORKERS` 覆盖；直接 `npm run test` 不带 cap），rust lane cargo `-j` 默认 min(4, 核数)（`AGENT_LEGION_RUST_WORKERS` 覆盖）；CI 4 核 runner 上这些默认值与不设上限时的并行度相当。多 worktree 并行开发时如仍抢 CPU，把这些 env 再调低（建议值 ≈ CPU 核数 ÷ 并行 worktree 数）。
- 同一 worktree 内不允许并发跑测试：`check-quick.sh` 已用 `.quick-gate.lock` 串行化（后来者等待，崩溃残留自动回收）；直接 `uv run pytest` 不受锁保护，必须自己确保没有其他测试进程在跑——测试库按 worktree 共享、xdist worker schema 固定为 gw0..gwN，两个进程并发会互相 TRUNCATE（现场症状：随机测试报 "Bootstrap is only available before the first user exists" 等 setup 错误，单跑必过）。
- 不要污染主工作区或他人 worktree 的运行时数据。
- 生产 worktree（如 `.worktrees/prod`）禁止 debug 与改代码：只允许 `git pull` 拿正式代码与 `make native-prod-up/down` 启停服务（prod-up 启动前会经 `scripts/ensure-velites.sh` 按 velites/ 源码指纹检测并自动重建过期的 velites 二进制）。所有修复与调试（含 Docker 容器调试）必须在 develop worktree 进行，经 PR → main → prod pull 到达生产。生产命令（`native-prod-*` / `stack-prod-*`）只在 prod worktree 跑，在其他 worktree 跑会抢生产端口并连错数据库。

## 2. Agent Tool Discipline

- 编辑现有文件用 `Edit`，新建文件用 `Write`；不要用 shell `sed` / `echo` / `cat <<EOF` 直接改文件。
- 批量搜索用 `Glob` / `Grep` / `Read`；不要直接用 `find` / `grep` / `rg`。
- 不要私自执行 `git commit` / `push` / `reset` / `rebase`；必须得到用户明确授权。
- 不要读取或修改工作目录外的文件（除非用户显式要求）。

## 3. Language & Interaction

- 用中文回复用户；代码、命令、路径、标识符保持原文。
- auto 模式下对小事自行决策，不反复询问。
- 使用相关 Superpowers skill；进入计划模式、多文件修改、架构决策前使用 `EnterPlanMode`。

## 4. Quality Gates（必须执行）

- 任何代码修改后先跑 `./scripts/check-quick.sh`。
- quick gate 的 backend lane 同时跑 `worker/ui/app.test.mjs`（node:test，无 node 时跳过并提示）；
  CI 侧在 backend-postgres-a job 执行同一入口。
- 提交或交接前确认 GitHub Actions full gate 通过（`.github/workflows/quality-gate.yml`
  的 backend-unit、backend-postgres-a/b/c、frontend-logic、frontend-component、
  frontend-coverage、e2e-smoke、rust、docker-build 等 job）；CI 不可用时本地跑
  `./scripts/check.sh` 代替。
- 运行 `make install-hooks` 启用版本化本地门禁：pre-commit 跑 fast gate，pre-push
  默认跑 smoke 级（静态 + 精选 smoke 测试层，成员见 `tests/conftest.py`；按推送路径
  裁剪 lane：纯前端改动跳过 backend pytest、纯 `velites/` 改动只跑 rust lane、docs
  改动只跑静态、共享文件/新分支一律全量）。用 `AGENT_LEGION_GATE_LEVEL=quick`
  （完整 quick 套件）或 `AGENT_LEGION_GATE_LEVEL=full`（本地 full gate）升级单次
  推送。full gate 由 GitHub CI 在 PR/push 执行，并按变更路径裁剪 lane
  （与本地 pre-push 一致：纯前端改动跳过 backend pytest shards 但保留
  api:check、纯 `velites/` 改动只跑 rust、`Dockerfile`/依赖锁/`worker/`/
  `shared/`/`deploy/` 改动加跑 docker-build 镜像构建 lane（CI-only）、
  docs-only 全跳过、共享文件全量，检测逻辑见 workflow 的 `changes` job）；push 触发只留
  main/master（develop 合并已由 PR gate 覆盖）；ci-extended 压力门与
  nightly-e2e 改为每周 schedule + 手动 dispatch，schedule 不重复跑
  普通 lane。
- 不要使用 `git commit --no-verify` 或 `git push --no-verify` 绕过本地质量门。
- 禁止在质量门未通过时声明完成。
- 后端测试隔离基于 TRUNCATE：每个 xdist worker 每 session 只建一次 schema，每个测试
  清空所有表（`tests/conftest.py`）。改动 DDL 的测试必须加 `@pytest.mark.fresh_schema`
  走完整重建。本地 quick gate 默认不带覆盖率（`AGENT_LEGION_COV=1` 开启；85% floor 由
  CI 与 `./scripts/check.sh` 强制）。pytest worker 数默认 min(4, 核数)，用
  `AGENT_LEGION_TEST_WORKERS` 覆盖。
- 新测试必须放进对应子系统子目录（如 `tests/services/`、`tests/scripts/`），不要新增
  `tests/` 根目录文件（静态检查 `scripts/architecture/test_placement.py` 强制，基线
  `config/architecture/test-root-files-baseline.json`）；确定不碰数据库的纯静态测试可加
  `@pytest.mark.no_db` 跳过 TRUNCATE 隔离。

## 5. Architecture Governance

- 修改边界/并发/安全/持久化数据前，先读 `config/architecture/`。
- 新增 invariant 或临时豁免要同步更新 registry。
- spec / plan 必须包含 `Quality Impact` 小节。
- 不要手写 frontend transport types，必须从 `frontend/src/generated/api.ts` 派生。
- 超出体积预算的文件必须拆分或回退，不能手动抬高 ceiling。ceiling 按有效行数计
  （排除注释行与空行），不要为凑预算压缩注释；`max_lines` 绝对上限按原始行数计。

## 6. Boundary Rules（禁止模式摘要）

- Workspace API 扩展顺序：contract → service → focused route。
- 用户鉴权经 `server/app/auth/dependencies.py` 注入（`require_user` /
  `require_admin` / `require_workspace_access`），不要在路由里手写 cookie /
  token 解析；公开端点仅限 `/api/health` 与 `/api/auth/login|bootstrap`。
- 测试中受保护 API 走 `client` fixture（自动 bootstrap admin 并带 CSRF
  header）；匿名行为用 `anon_client`。不留 auth 开关。
- Workspace 执行面扩展顺序：capability →（Agent 定义 或 node code）→ node limit（节点级并发）。
  executor 定义 / allocation / binding 概念已随 P-0.5 退役（schema v47 drop 两表）：非 Agent
  路由节点一律进隐含 code 池，池容量 = 实例设置 `code_capacity`，lease 行写常量 `'code'`
  （EXEC-CODE-POOL-001）。
- Phase 6 Job 边界：route 不做 DAG 遍历和文件系统删除。
- Workflow Node 只声明 `capability`，不声明 `runner` / `agent` / `skill` / command template。
- Job 执行服务通过 `server.app.executors.leases` 申请容量，不要直接调用 `executors.code` / `.runtime` / `.contracts`。
- code 节点：capability 不再声明 `path`（#96 已退役该绑定）；所有节点代码以
  DB 发布文本（`versioned_entities` entity_type `node_code`）为准，经发布流生效、版本不可变、
  job intake 冻结代码版本（EXEC-CODE-002），禁止任何运行时 API 增删改 repo 文件。
  `workflow_nodes/` 只剩示例
  workflow 的两个 git 评审种子源（启动时 seed-if-absent 发布为 global 作用域 node_code，
  `server/app/services/demo_node_seed.py`）；示例 workflow 的出厂 Agent 模板钉在
  `server/app/agent_catalog_builtin.py`（workspace 作用域 seed-if-absent，admin 编辑不被
  种子覆盖）。
  节点入口推荐 `def run(ctx)` + 节点 SDK 的 `@entrypoint` 装饰器（经典
  `run(job, job_dir, runtime)` 签名继续受支持）；节点内部的通用脚手架统一走节点 SDK
  `workspace_libs/node_sdk.py` 的 `NodeContext`（artifact 读写、service_config 合并、
  checkpoint、batch_payload、auth 上报）与姊妹模块 `workspace_libs/http_client.py`
  （联网机制）/ `workspace_libs/download.py`（SSRF 守卫 + 流式下载）/
  `workspace_libs/media.py`（SRT/ffprobe）——框架层不收业务语义（服务特定的
  URL 规则、payload 解析、质量阈值留在节点里）。不要在新节点里手写
  JSON 读写/配置合并/取消检查；节点运行时不含 DB 句柄或 DSN——batch、skill_versions 等
  DB 派生输入由父进程预取进 runtime，特权动作（连接 token 失效）由节点写 marker、
  父进程执行（EXEC-CODE-004，设计见
  `docs/architecture/node-sdk-and-worker-execution-design.md`）。
- 所有节点代码执行必须经 `velites sandbox wrap` OS 沙箱，沙箱不可用即拒绝执行
  （fail-closed，EXEC-CODE-003；#96 后 Host 本地也不再有裸子进程路径）；开关
  `workflows.custom_nodes_enabled`。
- code 节点上 Worker（批次 2，协议 v2，EXEC-CODE-WORKER-001）：worker-eligible =
  对解析后代码文本做静态 import 闭包扫描，闭包 ⊆ `workspace_libs` + stdlib
  （+ requests）才可上 Worker；示例 workflow 的两个纯 stdlib 节点全部
  Worker-eligible。
  无在线 code Worker 时 dispatch 探测并回落本地 executor（兜底=本地，不做
  queued 超时回落）。Worker 上所有 code 执行（内置与自定义）统一过 velites
  沙箱；Worker 与 Host 的容量按 kind 分池（`max_concurrency` /
  `max_code_concurrency`）各自记账各自强制。
- 节点可调参数经 `AgentDefinition.config_schema` 声明（`server/app/config_schema.py`
  子集）；code 节点经节点 `config_schema:` 块声明（随 revision 快照版本化）。优先级
  = agent 定义 → 节点 config_schema（executor 层声明已随 P-0.5 退役）；平台保留执行键
  `timeout_seconds`（integer，default 600，ge 1）/ `sandbox_network`（boolean，
  default false）自动合并进每个 code 路由节点的有效 schema（节点 config_schema
  不得重声明；v47 收割已把原 executor 层的 timeout/network 值搬到节点 `config`）。解析链
  defaults → 节点 `config` → workspace 覆盖，intake 冻结；manifest 仅携带白名单
  非敏感键（CONFIG-MANIFEST-001），敏感参数标记 `secret`。
- velites（`velites/` crate，自研 Rust harness）：pi、openclaw、velites 是平级
  runtime，由 `AgentDefinition.runtime` 声明。Agent 定义存 DB
  （`versioned_entities` 表，workspace 作用域（schema v46，解析严格限定本
  workspace、零全局兜底），经 Studio「Agent 管理」/ `/api/agent-definitions`
  （`workspace_id` 查询参数）管理，draft → published → archived 生命周期，
  版本不可变，灰度/回退走 publish/rollback），不再走 yaml——`agents:` 段与
  `workflows.pi` 块已退役，
  出现在任何 split yaml 启动即报错（fail-fast 带迁移指引）。runtime 直接钉死
  命令构建器与二进制（pi → pi argv，velites → velites argv；openclaw 未实现
  即报错），没有 flavor 之类的实现选择层。pi 作为可选 runtime 长期保留
  （不退役），新增 agent 按需要直接声明目标 runtime。Workflow 节点不感知
  runtime/harness 实现。Worker 声明某 runtime 前必须先在 PATH 提供对应二进制
  （启动预检缺失即拒启动）。
  事件 schema 改动必须同步 `velites/schema/events.schema.json`
  （`cargo run --bin velites-schema -- schema/events.schema.json`）并保证契约测试
  （`velites/tests/schema_current.rs`、`golden_events.rs`）通过；事件流只保留 Host
  消费的 pi 兼容子集，禁止引入 delta 事件（`message_update` /
  `tool_execution_update`）。
- Agent 执行的 provider/model/thinking 解析链：节点 `execution.*` 覆盖 →
  workspace Settings 默认（`default_agent_*` 三列）→ 报错，无 yaml/全局兜底；
  manifest 只携带解析后的 `execution.*` 块（enqueue 冻结 + claim 重解析，节点
  覆盖随 revision 升级实时生效，EXEC-RUNTIME-DISPATCH-001）。一个 capability
  在每个 workspace 只允许一个 published Agent（DB partial unique index 兜底）。
  测试的 Agent 目录由 `tests/helpers.seed_workspace_agent_definitions` 按测试所属
  workspace 播种（API 创建并绑定 demo workflow 的 workspace 由
  ensure_active_revision 自动 seed），不从 yaml sync。
- 多步变更必须先全部校验/备妥再统一应用：中间结果放临时变量，全部成功后
  一次性赋值生效，禁止半应用状态；跨进程/跨事务动作（killpg、目录迁移、
  重排队）前必须重新校验目标身份与状态。这是代码评审最高发的缺陷族。

典型反例：

```yaml
# Wrong: Workflow leaks implementation details.
review_keywords:
  runner: pi
  skill: education-video-problems-generation/review-questions

# Correct: Workflow declares business capability only.
review_keywords:
  capability: review_keywords
```

```python
# Wrong: Job service invokes an Executor directly.
from server.app.executors.code import CodeExecutor
CodeExecutor(...).execute(context)
```

更多完整规则与示例见 [docs/architecture/workspace-executor-evidence-matrix.md](docs/architecture/workspace-executor-evidence-matrix.md)。

## 7. Pi / External Skills

- Skill 只在外部仓库（如 `~/.agents/skills/agent-legion/...`）修改，不要复制或 symlink 到项目根。
- skill 源与锁已产品化：声明（`{repo, ref}`）与解析后的 commit 锁存 DB
  `global_settings`（key=`skill_sources` / `skill_lock`）；tracked
  `config/skills.yaml` / `config/skills.lock` 已退役，残留文件只在 DB 无记录时
  启动 import-once（warning）作一次性迁移通道，此后不再读取。
- 变更流程：外部仓库改 skill 并打 tag → admin UI（/admin/settings「Skill 源管理」）
  或 admin API（`PUT /api/admin/skill-sources/{skill_key}`）更新 ref → relock
  （`POST /api/admin/skill-sources/relock`，或 CLI `make skills-lock` /
  `uv run python -m server.app.skills.lock`）解析并冻结 commit。
- 修改后同步 shared assets，跑 `UV_CACHE_DIR=.uv-cache uv run python scripts/check-skills-shared.py`
  验证共享引用文件一致（skill 清单来自 `server/app/skills/builtin_sources.py` 常量）。
- 完整流程见 [README.md](README.md) 的 Agent Runtimes 章节。

## 8. Security & Data

- `data/` 不提交，配置与密钥不外传。
- Secret 值必须经 vault（Fernet 加密落 `workspace_secrets`；实例级外部服务凭据落
  `instance_secrets`），配置与快照只存 `secret_ref`，不得明文落库、出 API 或进日志
  （VAULT-SECRET-001）。
- Tracked config yaml（`config/*.yaml`）不得包含 secret 值：CMS 等外部服务的凭据与
  端点配置统一走实例级外部服务连接（admin 全局设置「外部服务连接」，DB
  `external_connections` + 实例 vault，SECURITY-EXTERNAL-CONNECTION-001），节点/workspace 配置
  只引用连接 key；env `CMS_*` / `AGENT_LEGION_CMS_TOKEN` 通道已退役（启动迁移收编进
  连接后硬切）；全局 `cms:` 段已从 split yaml 退役（出厂默认值在 capability
  config_schema），split yaml 写 `cms:` 撞 owned-key 校验报错；explicit 单文件配置出现
  `cms.token` / `cms.token_gen` 启动即报错（G2）；`openclaw` 段已从 split yaml 退役进实例设置（DB
  `global_settings` 的 `instance` 文档，`/api/admin/instance-settings` 维护），
  split yaml 写 `openclaw:` 撞 owned-key 校验报错；`openclaw.skill_safety`
  写 `ref` 在启动校验与实例设置 API（422）都会被拒（G3，ref 以 DB
  `skill_lock` 文档为唯一权威）。`asr` 段已随 `config/agent_legion.yaml`
  整体退役（文件存在即启动报错，带迁移指引）：业务参数与机器路径
  随业务转录节点一并迁出，平台不再有 ASR 配置通道。
  `vault` / `auth` 段为 env-only，写进任何 split yaml 会触发 owned-key 校验失败
  （CONFIG-YAML-001）。
- OpenClaw / Pi 命令模板来自本地配置，不要把 API key 写进命令行或日志。

## 9. Where to look next

- 项目结构 / 运行细节：[README.md](README.md) / [docs/architecture/](docs/architecture/)
- 远程执行运维手册：[docs/remote-execution-runbook.md](docs/remote-execution-runbook.md)
