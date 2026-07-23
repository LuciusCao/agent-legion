# Scripts

本目录包含 Agent Legion 的质量门、架构治理、迁移 helper 和生成器脚本。

## 质量门

| 脚本 | 用途 |
|------|------|
| `check-quick.sh` | 日常快速质量门：先并行 backend/frontend 静态检查，再并行 pytest/Vitest，避免静态检查与两套测试 runner 同时争抢 CPU。 |
| `check-quick-backend.sh` | quick gate 后端 lane；支持 `BACKEND_GATE_PHASE=static\|test\|all`。 |
| `check-quick-frontend.sh` | quick gate 前端 lane；支持 `FRONTEND_GATE_PHASE=static\|test\|all`，并通过 `FRONTEND_TEST_MODE=test\|coverage` 选择 Vitest 模式。 |
| `check-fast.sh` | pre-commit 实际调用的 fast gate：ruff/mypy/前端 lint，不跑测试。 |
| `check.sh` | 完整质量门（提交前）：coverage 模式 quick gate + 并行的 full backend evidence/前端 bundle，避免重复 Vitest 与 typecheck。 |
| `check-ci.sh` | CI 质量门：完整 gate 的 CI 扩展版本。 |
| `check-deps-audit.sh` | 依赖漏洞审计（pip-audit + npm audit）；非阻塞，需网络。 |
| `run-local-gate.sh` | 对精确 commit 执行 quick/full gate，并在 Git common directory 记录可复用的本地通过凭证。 |

## 架构治理

| 脚本 | 用途 |
|------|------|
| `check_architecture.py` | 检查模块边界、路由响应模型、源文件体积预算。 |
| `check_invariants.py` | 校验 `config/architecture/architecture-invariants.yaml` 与 `architecture-exemptions.yaml`。 |
| `ratchet_architecture_budgets.py` | 更新 `config/architecture/architecture-budgets.json` 基线；拒绝抬高 ceiling。 |
| `generate_architecture.py` | 从代码 AST 自动生成 `docs/architecture/backend.md`、`frontend.md`、`pipeline.md`、`deployment.md` 的表格章节。 |
| `generate_architecture_frontend.py` | `generate_architecture.py` 的前端路由提取 helper。 |
| `generate_architecture_pipeline.py` | `generate_architecture.py` 的视频 pipeline 节点提取 helper。 |

## Agent Worker 子系统

Worker 执行进程、Worker Service、Supervisor、配置存储与 CLI 已迁至顶层 `worker/` 包
（`worker/executor.py`、`worker/service.py`、`worker/supervisor.py`、`worker/config_store.py`、
`worker/client.py`、`worker/cli.py`、`worker/cleanup.py`），控制台静态资源在 `worker/ui/`。

## Spec / Skill 治理

| 脚本 | 用途 |
|------|------|
| `verify_specs.py` | 检查 design specs 的引用健康，自动分类到 `specs/`、`completed/`、`archive/`，并生成 `SPEC_HEALTH.md`。 |
| `check-skills-shared.py` | 校验外部 Pi skill 仓库与项目共享引用文件的一致性。 |

## 迁移与工具

| 脚本 | 用途 |
|------|------|
| `generate-api-types.sh` | 导出后端 OpenAPI 并生成 `frontend/src/generated/api.ts`；`--check` 只做漂移检查。 |
| `export_openapi.py` | 不启动 Worker 导出 OpenAPI 模式。 |
| `install-git-hooks.sh` | 配置 worktree 兼容的版本化 pre-commit / pre-push 钩子。 |
| `check-pi.sh` | Pi CLI 环境 smoke 检查。 |
| `view-session.py` | 将 OpenClaw session JSONL 渲染为人类可读的对话日志。 |
| `compare_skill_cost.py` | 按 skill 版本对比 token 成本与重试行为（共享逻辑在 `_skill_cost_core.py`）。 |

一次性脚本（`diagnose_cms.py`、`cleanup-agent-pollution.py`、`backfill-node-run-dirs.py`、`archive/backfill_source_uuid.py`）已于 2026-07-22 退役删除；一次性迁移脚本（`import-sqlite-to-postgres.py` + `sqlite_import_support.py`、`migrate-config-layout.py`）已于 2026-07-23 随 SQLite→PostgreSQL 迁移与配置布局拆分完成退役删除；历史用法见 git 历史。

## 子目录

| 目录 | 用途 |
|------|------|
| `architecture/` | `check_architecture.py` / `ratchet_architecture_budgets.py` 的检查实现（预算盘点、边界、路由契约、import 环等各 phase 模块）。 |
| `git-hooks/` | 版本化的 pre-commit / pre-push 钩子 dispatcher，由 `install-git-hooks.sh` 安装到 Git common directory，再转发到 worktree 根的 `.githooks/`。 |
| `remote/` | 远程 LLM 网关（`llm_gateway.py` 及 HTTP/SSE/stream/config 模块），见 `docs/remote-execution-runbook.md`。 |
| `stress/` | 压力测试：`simulate_agents.py` 合成负载生成器、`run_e2e_stress.py` 端到端压测 runner。 |

## 约定

- 新脚本统一使用下划线命名（`check_xxx.py`）；连字符命名（`check-skills-shared.py` 等）为存量，不强改。
- 包内可导入的脚本通过 `uv run python -m scripts.<name>` 运行，不再复制 `sys.path` bootstrap；
  同包共享逻辑直接 `from scripts._xxx import ...`。
- 以下存量场景保留 `sys.path` bootstrap：`worker/executor.py`（Docker 已改为整体拷贝 `worker/` 包，bootstrap 仅为兼容直接以脚本方式运行）、
  `stress/`（非包目录，按路径直接执行）。

## 运行方式

脚本通常通过 `Makefile` 调用，例如：

```bash
make check-quick
make check
make check-ci
make install-hooks
make skills-lock
make api-generate
make architecture-check
make architecture-ratchet
```

直接运行 Python 脚本时，建议使用项目虚拟环境：

```bash
UV_CACHE_DIR=.uv-cache uv run python -m scripts.<name>
```
