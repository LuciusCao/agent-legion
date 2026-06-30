# Agent Legion — Agent Operating Manual

本文件只包含 AI Agent 在修改本仓库时必须遵守的纪律与红线。
项目概览、安装、运行命令见 [README.md](README.md)；架构与实现细节见 [docs/architecture/](docs/architecture/)。

## 1. Worktree & Isolation

- 每次独立开发任务优先在新的 git worktree 中进行。
- 不同 worktree 使用不同 backend/frontend 端口与独立 `data/` 目录，避免 SQLite、视频、日志、package 互相覆盖。
- 创建新 worktree 后，从基准 worktree 复制 `.env` 到新的 worktree 根目录，确保测试、后端服务与外部集成配置一致。
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
- 提交或交接前必须跑 `./scripts/check.sh`。
- 禁止在质量门未通过时声明完成。

## 5. Architecture Governance

- 修改边界/并发/安全/持久化数据前，先读 `config/architecture/`。
- 新增 invariant 或临时豁免要同步更新 registry。
- spec / plan 必须包含 `Quality Impact` 小节。
- 不要手写 frontend transport types，必须从 `frontend/src/generated/api.ts` 派生。
- 超出体积预算的文件必须拆分或回退，不能手动抬高 ceiling。

## 6. Boundary Rules（禁止模式摘要）

- Workspace API 扩展顺序：contract → service → focused route。
- Workspace Executor 扩展顺序：capability → executor → allocation → binding → local limit（仅 local executor）。
- Phase 6 Job 边界：route 不做 DAG 遍历和文件系统删除。
- Workflow Node 只声明 `capability`，不声明 `runner` / `agent` / `skill` / command template。
- Job 执行服务通过 `server.app.executors.leases` 申请容量，不要直接调用 `executors.local` / `.pi` / `.openclaw` / `.runtime` / `.registry`。

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
# Wrong: Generic Workspace route imports a Video Hive phase.
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
- 跑 `UV_CACHE_DIR=.uv-cache uv run python scripts/check-skills-shared.py` 验证字节一致。
- 完整流程见 [README.md](README.md) 的 Pi Agent Runner 章节。

## 8. Security & Data

- `data/` 不提交，配置与密钥不外传。
- OpenClaw / Pi 命令模板来自本地配置，不要把 API key 写进命令行或日志。

## 9. Video Knowledge Workspace

- Knowledge video work lives in the `video_knowledge` workspace workflow.
- Job Detail video UI uses `VideoContentPanel`.
- Do not add new `/api/videos` or `/video-hive` behavior.
- Do not read legacy video tables in runtime paths.

## 10. Where to look next

- 项目结构 / 运行细节：[README.md](README.md) / [docs/architecture/](docs/architecture/)
- 设计规格与计划：[docs/superpowers/](docs/superpowers/)
- 已知问题：[issues/open](issues/open) / [issues/closed](issues/closed)
