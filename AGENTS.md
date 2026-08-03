# Agent Legion — Agent Operating Manual

本文件只包含 AI Agent 在修改本仓库时必须遵守的纪律与红线。
项目概览、安装、运行命令见 [README.md](README.md)；架构与实现细节见 [docs/architecture/](docs/architecture/)。

## 1. Worktree & Isolation

- 每次独立开发任务优先在新的 git worktree 中进行。
- 不同 worktree 使用不同 backend/frontend 端口与独立 `data/` 目录，避免 SQLite、视频、日志、package 互相覆盖。
- 创建新 worktree 后，从基准 worktree 复制 `.env` 到新的 worktree 根目录，确保测试、后端服务与外部集成配置一致。
- 推荐用 `scripts/init-worktree.sh` 一键完成初始化（幂等）：复制 `.env`、按 worktree 名派生并创建专属 Postgres 库、生成缺失的 `deploy/secrets/agent_worker_register_token` 与 `deploy/secrets/vault_master_key`（缺这两个文件会导致 pytest/服务启动即失败）。手工初始化时必须自行补上这两个 secrets 文件。
- 新 worktree 必须配置独立 Postgres 数据库：在 `.env` 中加 `AGENT_LEGION_DATABASE_URL` 指向专属库（不要用 tracked 的 `config/app.yaml` 里的共享库）。共享库会让任一 worktree 的进程启动（含质量门里的 `export_openapi`）清掉其他实例的 `worker_control_state` 等运行时状态。
- 测试库无需手动配置：`tests/postgres_support.py` 按 worktree 目录名派生专属测试库（`agent_legion_test_<worktree>`）并在首次测试运行时自动建库；只有需要覆盖时才设 `AGENT_LEGION_TEST_DATABASE_URL`。
- 多 worktree 并行开发时，必须在每个 worktree 的 shell 里 `export AGENT_LEGION_TEST_WORKERS=4`（建议值 ≈ CPU 核数 ÷ 并行 worktree 数）。不设置时 pytest `-n auto` 会让每个 worktree 吃满所有核，互相拖慢并打满共享 Postgres。
- 不要污染主工作区或他人 worktree 的运行时数据。

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
- 提交或交接前确认 GitHub Actions full gate 通过（`.github/workflows/quality-gate.yml`
  的 `backend` + `frontend` job）；CI 不可用时本地跑 `./scripts/check.sh` 代替。
- 运行 `make install-hooks` 启用版本化本地门禁：pre-commit 跑 fast gate，pre-push
  默认跑 smoke 级（静态 + 精选 smoke 测试层，成员见 `tests/conftest.py`；按推送路径
  裁剪 lane：纯前端改动跳过 backend pytest、纯 `velites/` 改动只跑 rust lane、docs
  改动只跑静态、共享文件/新分支一律全量）。用 `AGENT_LEGION_GATE_LEVEL=quick`
  （完整 quick 套件）或 `AGENT_LEGION_GATE_LEVEL=full`（本地 full gate）升级单次
  推送。full gate 由 GitHub CI 在 PR/push 执行；ci-extended 压力门改为
  nightly + 手动 dispatch。
- 不要使用 `git commit --no-verify` 或 `git push --no-verify` 绕过本地质量门。
- 禁止在质量门未通过时声明完成。
- 后端测试隔离基于 TRUNCATE：每个 xdist worker 每 session 只建一次 schema，每个测试
  清空所有表（`tests/conftest.py`）。改动 DDL 的测试必须加 `@pytest.mark.fresh_schema`
  走完整重建。本地 quick gate 默认不带覆盖率（`AGENT_LEGION_COV=1` 开启；85% floor 由
  CI 与 `./scripts/check.sh` 强制）。多 worktree 并行时用 `AGENT_LEGION_TEST_WORKERS`
  限制 pytest worker 数（默认 `-n auto` 吃满所有核）。
- 新测试必须放进对应子系统子目录（如 `tests/services/`、`tests/scripts/`），不要新增
  `tests/` 根目录文件；确定不碰数据库的纯静态测试可加 `@pytest.mark.no_db` 跳过
  TRUNCATE 隔离。

## 5. Architecture Governance

- 修改边界/并发/安全/持久化数据前，先读 `config/architecture/`。
- 新增 invariant 或临时豁免要同步更新 registry。
- spec / plan 必须包含 `Quality Impact` 小节。
- 不要手写 frontend transport types，必须从 `frontend/src/generated/api.ts` 派生。
- 超出体积预算的文件必须拆分或回退，不能手动抬高 ceiling。

## 6. Boundary Rules（禁止模式摘要）

- Workspace API 扩展顺序：contract → service → focused route。
- 用户鉴权经 `server/app/auth/dependencies.py` 注入（`require_user` /
  `require_admin` / `require_workspace_access`），不要在路由里手写 cookie /
  token 解析；公开端点仅限 `/api/health` 与 `/api/auth/login|bootstrap`。
- 测试中受保护 API 走 `client` fixture（自动 bootstrap admin 并带 CSRF
  header）；匿名行为用 `anon_client`。不留 auth 开关。
- Workspace Executor 扩展顺序：capability → executor → allocation → binding → local limit（仅 local executor）。
- Phase 6 Job 边界：route 不做 DAG 遍历和文件系统删除。
- Workflow Node 只声明 `capability`，不声明 `runner` / `agent` / `skill` / command template。
- Job 执行服务通过 `server.app.executors.leases` 申请容量，不要直接调用 `executors.local` / `.pi` / `.openclaw` / `.runtime` / `.registry`。
- 节点可调参数经 `AgentDefinition.config_schema` 声明（`server/app/config_schema.py`
  子集）；executor 节点经 capability 的 `LocalCapabilityConfig.config_schema`
  声明（agent 优先、executor 兜底）。解析链 defaults → 节点 `config` →
  workspace 覆盖，intake 冻结；manifest 仅携带白名单非敏感键
  （CONFIG-MANIFEST-001），敏感参数标记 `secret`。
- velites（`velites/` crate，自研 Rust harness）：pi、openclaw、velites 是平级
  runtime，由 `AgentDefinition.runtime` 声明（`config/workflow.yaml` `agents:` 段，
  per-agent 灰度/回退都是单字段改动）。`workflows.pi.flavor` 只作用于
  `runtime: pi` 的 agent 做实现选择；`runtime: velites` 钉死 velites 实现、忽略
  flavor。Workflow 节点不感知 runtime/harness 实现。Worker 声明 velites 前必须
  先在 PATH 提供 velites 二进制（启动预检缺失即拒启动）。
  事件 schema 改动必须同步 `velites/schema/events.schema.json`
  （`cargo run --bin velites-schema -- schema/events.schema.json`）并保证契约测试
  （`velites/tests/schema_current.rs`、`golden_events.rs`）通过；事件流只保留 Host
  消费的 pi 兼容子集，禁止引入 delta 事件（`message_update` /
  `tool_execution_update`）。

典型反例：

```yaml
# Wrong: Workflow leaks implementation details.
review_keywords:
  runner: pi
  skill: question_comprehension_info/review_key_info

# Correct: Workflow declares business capability only.
review_keywords:
  capability: review_keywords
```

```python
# Wrong: Generic Workspace route imports a legacy video pipeline phase.
from server.app.pipeline.download import download_video
```

```python
# Wrong: Job service invokes an Executor directly.
from server.app.executors.local import LocalExecutor
LocalExecutor(...).execute(context)
```

更多完整规则与示例见 [docs/architecture/workspace-executor-evidence-matrix.md](docs/architecture/workspace-executor-evidence-matrix.md)。

## 7. Pi / External Skills

- Skill 只在外部仓库（如 `~/.agents/skills/agent-legion/...`）修改，不要复制或 symlink 到项目根。
- 修改后同步 shared assets，更新 `config/skills.yaml` 与 `config/skills.lock`。
- 跑 `UV_CACHE_DIR=.uv-cache uv run python scripts/check-skills-shared.py` 验证共享引用文件一致。
- 完整流程见 [README.md](README.md) 的 Pi Agent Runner 章节。

## 8. Security & Data

- `data/` 不提交，配置与密钥不外传。
- Secret 值必须经 vault（Fernet 加密落 `workspace_secrets`），配置与快照只存
  `secret_ref`，不得明文落库、出 API 或进日志（VAULT-SECRET-001）。
- Tracked config yaml（`config/*.yaml`）不得包含 secret 值：CMS token 只走 env
  （`AGENT_LEGION_CMS_TOKEN` / `CMS_*`，`BASECMS_*` 为 deprecated alias）或
  workspace resource binding + vault；
  yaml 出现 `cms.token` / `cms.token_gen` 启动即报错（G2），`openclaw.skill_safety`
  写 `ref` 启动即报错（G3，ref 以 `config/skills.lock` 为唯一权威）。
  `vault` / `auth` 段为 env-only，写进任何 split yaml 会触发 owned-key 校验失败
  （CONFIG-YAML-001）。
- OpenClaw / Pi 命令模板来自本地配置，不要把 API key 写进命令行或日志。

## 9. Video Knowledge Workspace

- Knowledge video work lives in the `video_knowledge` workspace workflow.
- Job Detail video UI uses `VideoContentPanel`.
- Do not add new `/api/videos` or `/video-hive` behavior.
- Do not read legacy video tables in runtime paths.

## 10. Where to look next

- 项目结构 / 运行细节：[README.md](README.md) / [docs/architecture/](docs/architecture/)
- 远程执行运维手册：[docs/remote-execution-runbook.md](docs/remote-execution-runbook.md)
