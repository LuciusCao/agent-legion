# Agent Legion — Agent Operating Manual

本文件只包含 AI Agent 在修改本仓库时必须遵守的纪律与红线。
项目概览、安装、运行命令见 [README.md](README.md)；架构与实现细节见 [docs/architecture/](docs/architecture/)。

## 0. 本文维护纪律

- 本文只收对 agent 的**现行行为约束**。退役公告、迁移指引、事故复盘、设计背景一律写进 docs/（架构类进 `docs/architecture/`），本文至多留一行指针 + invariant/issue 编号。
- 新增规则默认不超过 3 行、不写病史；觉得需要铺背景，说明它本该进 docs。
- 修改本文必须顺手删除过时内容，只增不删是反模式；总规模不得超过 32 KB（agent harness 注入上限），逼近上限时先压缩存量再新增。

## 1. Worktree & Isolation

- 每次独立开发任务优先在新的 git worktree 中进行。
- worktree 一律建为主仓库根的平级子目录：先 `cd` 到主仓库根（`git worktree list` 的第一个条目），再 `git worktree add .worktrees/<name> -b <branch> <base>`。禁止嵌套（在其他 worktree 里用相对路径 `git worktree add .worktrees/<name>` 会建进当前 worktree 内部）。
- 不同 worktree 使用不同 backend/frontend 端口与独立 `data/` 目录，避免数据库、视频、日志、package 互相覆盖。
- **创建 worktree 后必须立即跑 `scripts/init-worktree.sh`（强制，幂等），临时试验也不例外**：复制 `.env`（无法复制时 fail-fast）、按 worktree 名派生并创建专属 Postgres 库、派生 `AGENT_LEGION_S3_BUCKET`（endpoint 可达时建 bucket）、生成缺失的 `deploy/secrets/vault_master_key`、缺失时种子 worker 状态副本（#323 起 dev 侧不再有 `config/agent-worker.yaml`）。手工初始化必须自行补上 vault_master_key：它是 env-only（`AGENT_LEGION_VAULT_MASTER_KEY` / `_FILE`），缺 key 时 vault 写入与 `secret_ref` 解析抛 `VaultMasterKeyMissingError`。
- worker 注册用 workspace-scoped token：admin UI（workspace 设置 → Agent 与 Worker）按 workspace 签发，worker 控制台添加后按全部 token 重注册，见 [docs/agent-worker-deployment.md](docs/agent-worker-deployment.md)。遗留的全局 register token env / yaml 配置启动即报错（fail-fast 带迁移指引，#35）。
- 开发实例两个默认关闭的开关（刻意设计，防失控自跑）：后端每次启动把全部 workspace 重置为暂停，需恢复调度时跑 `scripts/resume-workspaces.sh`（**必须在后端首次启动建表之后**）或在控制台手动恢复；worker 的 `claim_enabled` 默认 false，经 worker 控制台或 `PUT /api/config` 打开。worker 唯一生效配置是状态副本 `data/agent-worker-service/worker.yaml`，修改一律走控制台/API（#323）。
- 新 worktree 必须在 `.env` 配置 `AGENT_LEGION_DATABASE_URL` 指向专属库：`database.url` 为 env-only，代码默认库是共享库（即 prod 库），不要依赖默认值。结构防线：`init_db` 对裸共享库名（`agent_legion`）要求 `AGENT_LEGION_ALLOW_SHARED_DB_SCHEMA=1` 才放行（prod 启动器已内置），`export_openapi` 直接拒绝在共享库上运行（背景见 [docs/postgresql-runbook.md](docs/postgresql-runbook.md)）。
- 测试库无需手动配置：`tests/postgres_support.py` 按 worktree 目录名派生专属测试库（`agent_legion_test_<worktree>`）并自动建库；只有需要覆盖时才设 `AGENT_LEGION_TEST_DATABASE_URL`。
- worktree 收尾（用户确认清理后）统一用 `scripts/clean-worktree.sh <worktree名> [--yes]` 一键清理 worktree/本地分支/派生库/派生 bucket，幂等可重跑、自带护栏；远端分支默认只提示不删（`--delete-remote-branch` 才删）。
- 只需单独清理派生库时（收尾场景已被 clean-worktree.sh 覆盖，它内部即转调本脚本）走 `scripts/drop-worktree-db.sh <worktree名>`，不要裸 `dropdb`：脚本自带护栏（按 worktree 名派生库名、以非 superuser role 连接，物理上碰不到共享/prod 库），role 设置见 [docs/postgresql-runbook.md](docs/postgresql-runbook.md)。
- 测试并行度默认克制：后端 pytest-xdist min(4, 核数)（`AGENT_LEGION_TEST_WORKERS` 覆盖）、前端 vitest 经 gate `--maxWorkers=4`（`AGENT_LEGION_FRONTEND_TEST_WORKERS` 覆盖）、rust `-j` min(4, 核数)（`AGENT_LEGION_RUST_WORKERS` 覆盖）。多 worktree 并行开发抢 CPU 时调低（建议 ≈ 核数 ÷ 并行 worktree 数）。
- 同一 worktree 内不允许并发跑测试：`check-quick.sh` 已用 `.quick-gate.lock` 串行化；直接 `uv run pytest` 不受锁保护，必须自己确保没有其他测试进程在跑——测试库按 worktree 共享、xdist schema 固定，两个进程并发会互相 TRUNCATE（症状：单跑必过的随机 setup 错误）。
- 不要污染主工作区或他人 worktree 的运行时数据。
- 生产 worktree（如 `.worktrees/prod`）禁止 debug 与改代码：只允许 `git pull` 与 `make prod-up` / `make prod-down`（prod-up 经 `scripts/ensure-velites.sh` 自动重建过期 velites 二进制）。所有修复与调试必须在 develop worktree 进行，经 PR → main → prod pull 到达生产。生产命令只在 prod worktree 跑，在其他 worktree 跑会抢生产端口并连错数据库。

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

- 修改-验证内环用 `GATE_TIER=aff ./scripts/check-quick.sh`：backend 按覆盖逆索引只跑受影响测试、前端 `vitest related`。aff 档不是 gate 凭证（`run-local-gate.sh` 拒绝该档）——无索引、索引盲区或选择面太宽时自动回落 unit 全量，回落只会更慢、不会漏跑。任何代码修改后至少跑一次完整 `./scripts/check-quick.sh`（aff 通过不能替代）。
- **quick gate 的完整档默认只跑 unit 层**（PostgreSQL 离线，`-m "not postgres and not repository_gate"`），postgres 集成层交给 CI（每个 PR 的 backend-postgres-a/b/c 必跑）。**碰数据库的改动（schema、migration、queries、routes 的 DB 行为）交接前必须显式跑 `GATE_TIER=postgres ./scripts/check-quick-backend.sh`**；本地全量替代品 `./scripts/check.sh` 含两层（unit 段 + postgres 追加段）。
- gate 内部 test 轮错峰：backend lane 先单独跑完，frontend/rust 随后并行；静态轮全并行。
- **机器级 gate 排队**：quick gate 经 git common dir 的 slot 排队（`scripts/gate-queue.sh`），默认 `AGENT_LEGION_MAX_PARALLEL_GATES=1`——同机串行、一次一个 gate 独占整机预算（大机器可显式设 2），后来者打印持有者并等待；每 lane worker 数按并发 gate 数均分，backend pytest 统一 `--dist worksteal`。排队本身就是正确行为，等待期做读代码/写代码等不占 CPU 的事。机制与实测依据见 [docs/architecture/local-quality-gates.md](docs/architecture/local-quality-gates.md)。
- **aff 索引纪律**：`.pytest-aff-index.json` 是 gitignore 的本地工件，每个 worktree 首次用 aff 前必须先跑 `GATE_TIER=aff-index ./scripts/check-quick-backend.sh` 建索引（约 2.5 分钟）；依赖或 `tests/conftest.py` 变更后重建。aff 输出含「aff fallback」时先建索引再继续内环。
- quick gate 的 backend lane 同时跑 `worker/ui/app.test.mjs`（node:test，无 node 时跳过并提示）；CI 侧在 api-check job 执行同一入口。
- 提交或交接前确认 GitHub Actions full gate 通过（`.github/workflows/quality-gate.yml` 的 backend-unit、api-check、backend-postgres-a/b/c、backend-coverage、frontend-logic、frontend-component、frontend-coverage、e2e-smoke、rust、docker-build 等 job）；CI 不可用时本地跑 `./scripts/check.sh` 代替。
- 运行 `make install-hooks` 启用版本化本地门禁：pre-commit 跑 fast gate，pre-push 默认跑 smoke 级（成员见 `tests/conftest.py`）并按推送路径裁剪 lane（纯前端跳过 backend pytest、纯 `velites/` 只跑 rust、docs 只跑静态、共享文件/新分支全量）。用 `AGENT_LEGION_GATE_LEVEL=quick` / `full` 升级单次推送。CI full gate 按同样的路径规则裁剪 lane（检测逻辑见 workflow 的 `changes` job；docs-only 变更仅 changes job 运行 + postgres shard 秒级空跑、其余 lane 跳过，跳过/空跑的 required check 在分支保护里算通过）；ci-extended 与 nightly-e2e 在 `.github/workflows/nightly-gate.yml` 独立执行。
- 不要使用 `git commit --no-verify` 或 `git push --no-verify` 绕过本地质量门。
- 禁止在质量门未通过时声明完成。
- 后端测试隔离基于 TRUNCATE：每个 xdist worker 每 session 只建一次 schema，每个测试清空所有表（`tests/conftest.py`）。改动 DDL 的测试必须加 `@pytest.mark.fresh_schema` 走完整重建。本地 quick gate 默认不带覆盖率（`AGENT_LEGION_COV=1` 开启；85% floor 由 CI 与 `./scripts/check.sh` 强制）。pytest worker 数默认 worktree 感知（`scripts/gate-jobs.sh`），用 `AGENT_LEGION_TEST_WORKERS` 覆盖。
- 新测试必须放进对应子系统子目录（如 `tests/services/`、`tests/scripts/`），不要新增 `tests/` 根目录文件（静态检查 `scripts/architecture/test_placement.py` 强制，基线 `config/architecture/test-root-files-baseline.json`）；确定不碰数据库的纯静态测试可加 `@pytest.mark.no_db` 跳过 TRUNCATE 隔离。
- 测试文件超过 800 行就应主动按被测主题拆分（同目录姊妹文件、用例零改动迁移）；gate 的 1000 行上限是硬底线。存量超 800 行的文件随下次触碰时顺手拆。

## 5. Architecture Governance

- 修改边界/并发/安全/持久化数据前，先读 `config/architecture/`。
- 新增 invariant 或临时豁免要同步更新 registry。
- spec / plan 必须包含 `Quality Impact` 小节。
- 宽捕获纪律（#204/#298）：`server/app` 与 `worker/` 下新增 `except Exception`（或裸 `except:`）必须带 `# #204 broad-except audit:` 注释（讲清失败语义、为什么吞、结果空间、日志保全），或收窄为具体异常族；无注释的宽捕获会被 `scripts/architecture/broad_except_audit.py` 拒绝。
- 概念退役 PR 必须同步在 `config/architecture/docs-retired-terms.yaml` 追加 pattern 条目，并清零现行文档命中（退役表述上下文豁免，语义见 `scripts/architecture/docs_retired_terms.py`）；现行文档白名单须与 `docs/architecture/README.md` 现行文档索引表同步。
- 不要手写 frontend transport types，必须从 `frontend/src/generated/api.ts` 派生。
- 超出体积预算的文件必须拆分或回退，不能手动抬高 ceiling。ceiling 按有效行数计（排除注释行与空行），不要为凑预算压缩注释；`max_lines` 绝对上限按原始行数计（#293 起声明式产物 root 可覆盖：`server/app/db` 的 `.sql` 与 `worker/ui` 的 `.js/.css` 各有 root 级 `max_lines`）。
- ceiling 单调只降不升（#209）：`check_architecture` 按 git 锚点拒绝**已跟踪条目**的任何上抬；唯一合法上抬通道是带 `remove_when` 的 `architecture.file_budget` 豁免。改名不重置 ceiling（git rename 检测沿用旧路径地板，#236）；真正的全新文件首次登记（actual + buffer）不受约束。release train（develop→main）例外：CI 在 `base=main && head=develop` 的 PR 与 main/master 合并后 push 重跑时设 `AGENT_LEGION_BUDGET_MONOTONICITY_RELEASE_TRAIN=1` 让锚点只看 HEAD（#249）；feature→develop 的 PR 与本地门禁保持 HEAD^ 基线锚点严格性。本地模拟 CI 的 PR 锚点判定：设 `AGENT_LEGION_BUDGET_BASE=origin/develop` 后锚点变为 HEAD + 该 base ref（release-train opt-out 优先；base ref 无法解析硬失败，按指引 fetch；边界基线守卫共用该覆盖）。

## 6. Boundary Rules（禁止模式摘要）

本节只收 agent 改代码时可能违反的红线；各子系统内部机制（调度、pin、解析链、物化细节）的权威记录是 [docs/architecture/workspace-executor-evidence-matrix.md](docs/architecture/workspace-executor-evidence-matrix.md) 与各模块 docstring。

- Workspace API 扩展顺序：contract → service → focused route。
- 新 service 的数据库访问必须走 `JobQueries` 门面（`server/app/jobs/queries`），不在 service 里手写 SQL、import `server.app.db.transaction`/`connection` 或读 DSN（唯一公开访问器 `dsn_identity` 仅限数据层自身与经豁免的毗邻组件，service 里读同样计入 ratchet）（BOUNDARY-DATA-001；基线只降不升，见 `config/architecture/service-data-boundary-baseline.json`）。
- 用户鉴权经 `server/app/auth/dependencies.py` 注入（`require_user` / `require_admin` / `require_workspace_access`），不要在路由里手写 cookie / token 解析；公开端点仅限 `/api/health` 与 `/api/auth/login|bootstrap`。
- 测试中受保护 API 走 `client` fixture（自动 bootstrap admin 并带 CSRF header）；匿名行为用 `anon_client`。不留 auth 开关。
- Workspace 执行面扩展顺序：capability →（Agent 定义 或 node code）→ node limit（节点级并发）。executor 概念已退役（schema v47）：非 Agent 路由节点一律进隐含 code 池（EXEC-CODE-POOL-001），不要复活 executor binding / allocation。
- Job 边界：route 不做 DAG 遍历和文件系统删除。
- Workflow 权威定义是 workspace 的 active revision，无全局 catalog、无列表/注册 API（DB-WORKFLOW-CATALOG-001）；workspace id 即 workflow key，创建后终身不可变——不要新增改 key 的接口、全局兜底或创建路径的种子逻辑（DB-WORKSPACE-KEY-BINDING-001，退役清单见 [docs/architecture/workflow-key-retirement-inventory.md](docs/architecture/workflow-key-retirement-inventory.md)）。
- Workflow Node 声明 `capability`、显式执行类型 `type: code | agent`（#284，schema v66）与可选的 `skill` 内容绑定（#76：`key` + 可选 `ref`，随 revision 版本化；仅 `agent` 节点可声明，`ref` 为空归一为 `latest` = 跟随仓库 HEAD）：`agent` 节点的 capability 发布时必须恰好解析到一个 published Agent，revision 发布只为 `agent` 节点物化路由；`code` 节点必须有 published node_code；Agent 发布/归档不改路由，遗留 `type: node` 与缺失由 loader 归一化为 `code`。dispatch 从节点取 skill（节点绑定优先，`AgentDefinition.skill` 降为可选 legacy 兜底，皆空即节点失败），manifest 记录 `skill` / `skill_ref` / `skill_version`（`ref@commit12`）/ `skill_commit`（完整 sha），Worker 结果 Host 侧 output 校验按 manifest `skill_commit` 精确物化同一版本（legacy manifest 回落 `(skill, skill_ref)` 重解析；执行副本一律 `git archive` 导出，零工作树写，EXEC-SKILL-NODE-001 / EXEC-SKILL-HERMETIC-001）。节点不声明 `runner` / `agent` / command template（见文末反例）。豁免 capability 且不 dispatch 的例外类型：`type: start` 入口（EXEC-WORKFLOW-START-001）与 `type: approval` 人工审批门（EXEC-APPROVAL-001，语义见 `server/app/workflows/approval_node.py` docstring）；条目类型契约见 MATERIAL-BUNDLE-001。
- Job 执行服务通过 `server.app.executors.leases` 申请容量，不要直接调用 `executors.code` / `.runtime` / `.contracts`。
- 节点代码与 Agent 定义以 DB 发布文本（`versioned_entities`）为准、版本不可变；普通 job 不 pin 版本，dispatch 只解析本 workspace 当前 published——不要加全局兜底、不要给普通 job 引入版本冻结（EXEC-CODE-002/003）。禁止任何运行时 API 增删改 repo 文件。
- 写节点统一走节点 SDK：入口推荐 `def run(ctx)` + `@entrypoint`，脚手架用 `workspace_libs/node_sdk.py` 的 `NodeContext` 与姊妹模块（`http_client.py` / `download.py` / `media.py`），不要手写 JSON 读写/配置合并/取消检查；节点运行时不含 DB 句柄——DB 派生输入由父进程预取，特权动作由节点写 marker、父进程执行（EXEC-CODE-004，设计见 [docs/architecture/node-sdk-and-worker-execution-design.md](docs/architecture/node-sdk-and-worker-execution-design.md)）。
- 所有节点代码执行必须经 `velites sandbox wrap` OS 沙箱，沙箱不可用即拒绝执行（fail-closed，EXEC-CODE-003）；开关 `workflows.custom_nodes_enabled`。Worker 上的 code 执行同样过沙箱，worker-eligible 由静态 import 闭包扫描判定（EXEC-CODE-WORKER-001）。
- 节点可调参数经 `AgentDefinition.config_schema` 或节点 `config_schema:` 块声明（`server/app/config_schema.py` 子集）；平台保留执行键 `timeout_seconds` / `sandbox_network` 不得重声明；解析链 defaults → 节点 `config` → workspace 覆盖，intake 冻结。敏感参数标记 `secret`，manifest 只携带白名单非敏感键（CONFIG-MANIFEST-001）；`runtime_mutable: true` 键每次 dispatch 重解析（CONFIG-RUNTIME-MUTABLE-001）。
- Agent 定义存 DB、不走 yaml——`agents:` 段与 `workflows.pi` 块出现在任何 split yaml 启动即报错。runtime（pi / velites）由 `AgentDefinition.runtime` 声明，经 `server/app/agent_runtime` catalog 的 adapter 钉死命令构建器与二进制（openclaw 曾短暂接入，因无流式事件与 token 计量已随 #75 整体退役；新 runtime 按 adapter 机制接入，步骤见 [docs/architecture/velites-harness.md](docs/architecture/velites-harness.md) 接入指南）；runtime 全集的单一事实来源是 catalog 的 `AGENT_RUNTIMES`（Literal/Worker 白名单/Worker 侧集合由 `tests/agent_runtime/test_runtime_catalog.py` 钉住全等）。Worker 按本机二进制探测自动启用 runtime，`disabled_runtimes` 反选（#254）。velites 事件 schema 改动必须同步 `velites/schema/events.schema.json` 并保证契约测试（`velites/tests/schema_current.rs`、`golden_events.rs`）通过；禁止引入 delta 事件（`message_update` / `tool_execution_update`）。
- Agent 执行的 provider/model/thinking 解析链：节点 `execution.*` → workflow 顶层 `execution` → 报错，不要加 workspace/yaml/全局兜底；解析结果按 catalog adapter 声明的 `ExecutionContract` 校验（必填缺失或配置了 runtime 不支持的键 → dispatch/claim fail-fast，EXEC-RUNTIME-DISPATCH-001）。一个 capability 每个 workspace 只允许一个 published Agent。测试的 Agent 目录用 `tests/helpers.seed_workspace_agent_definitions` 播种，不从 yaml sync。
- 多步变更必须先全部校验/备妥再统一应用：中间结果放临时变量，禁止半应用状态；跨进程/跨事务动作（killpg、目录迁移、重排队）前必须重新校验目标身份与状态。这是代码评审最高发的缺陷族。
- Job 产物权威副本在实例对象存储（`job_artifacts` 清单 + `server/app/services/job_artifact_objects.py`），本地 job_dir 只是执行暂存与可淘汰缓存（EXEC-ARTIFACT-STORE-001）。Worker 产物回传只走 claim 注入的 presigned S3 通道，禁止新增独立回传协议（EXEC-ARTIFACT-WORKER-001）；`/api/artifacts` 本地 CAS 是 legacy 兼容路径，不要加新功能。

典型反例：

```yaml
# Wrong: Workflow leaks implementation details.
review_keywords:
  runner: pi

# Correct: Workflow declares execution type and business capability only.
review_keywords:
  type: agent
  capability: review_keywords
```

```python
# Wrong: Job service invokes an Executor directly.
from server.app.executors.code import CodeExecutor
CodeExecutor(...).execute(context)
```

更多完整规则与示例见 [docs/architecture/workspace-executor-evidence-matrix.md](docs/architecture/workspace-executor-evidence-matrix.md)。

## 7. Pi / Skills

- Skill 只在其本地仓库修改，不要复制或 symlink 到项目根。skill 仓库的位置自便，但被节点绑定或 agent 定义引用的 skill 目录必须位于 skill root（`~/.agents/skills`）之下，skill key 为其下的两段相对路径 `<group>/<name>`。
- skill root 统一为 `~/.agents/skills`（单一来源 `server/app/skills/skill_roots.py`，实例设置只读展示）；workspace 的 agent skill 默认位于 `~/.agents/skills/<workspace_id>/`，SkillSelector 以只读前缀 + 相对目录名录入。skill 是 skill root 下的本地 in-place git 仓库（唯一模式，无注册表、无远程 clone 通道，#322）；缓存目录缺失即报错，按指引在 skill root 下创建。
- 节点 `skill.ref` 语义：`latest`（空 ref 已归一为它）= 跟随仓库 HEAD，每次 dispatch 现场解析、永不入锁；具体 tag = 首次 dispatch 把 commit 冻结进 DB `skill_lock`（v2 多值 `{repo, refs: {ref → commit}}`，repo 仅审计，EXEC-SKILL-NODE-001）。重解析已 pin 的 ref 走 CLI `make skills-lock`（遍历锁内已有条目）；admin `/api/admin/skill-sources*` 端点与「Skill 源管理」面板已随注册表一并删除。
- 完整流程见 [examples/README.md](examples/README.md)（demo skill 的接线方式）。

## 8. Security & Data

- `data/` 不提交，配置与密钥不外传。
- Secret 值必须经 vault（Fernet 加密落 `workspace_secrets`；实例级外部服务凭据落 `instance_secrets`），配置与快照只存 `secret_ref`，不得明文落库、出 API 或进日志（VAULT-SECRET-001）。
- Tracked config yaml（`config/*.yaml`）不得包含 secret 值：外部服务凭据与端点统一走实例级外部服务连接（admin 全局设置「外部服务连接」，DB `external_connections` + 实例 vault，SECURITY-EXTERNAL-CONNECTION-001），节点/workspace 配置只引用连接 key。`CMS_*` env 通道与全局 `cms:` / `asr:` / `agents:` / `workflows.pi` 段均已退役（写入即撞 owned-key 校验或启动报错，fail-fast 带迁移指引，治理见 [docs/architecture/agent-config-governance.md](docs/architecture/agent-config-governance.md)）；`openclaw` 段已随 openclaw runtime 整体退役（#75，实例设置文档读取时整块剥离、写入 422，`AGENT_LEGION_OPENCLAW_CWD` env 同步退役）；`vault` / `auth` 段为 env-only，写进任何 split yaml 触发 owned-key 校验失败（CONFIG-YAML-001）。
- Pi 命令模板来自本地配置，不要把 API key 写进命令行或日志。
- 开源卫生：tracked 文档、commit message、PR body 不得携带任一部署实例的生产数据规模与内部运维事实（具体 job 数、DB/产物体积、节点执行量、成功率、停机窗口安排等）；设计依据与运维指引一律用通用量级表述（如「存量较多时」「数 GB 级」）。

## 9. Where to look next

- 项目结构 / 运行细节：[README.md](README.md) / [docs/architecture/](docs/architecture/)
- 远程执行运维手册：[docs/remote-execution-runbook.md](docs/remote-execution-runbook.md)
