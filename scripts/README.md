# Scripts

本目录包含 Agent Legion 的质量门、架构治理、迁移 helper 和生成器脚本。

## 质量门

| 脚本 | 用途 |
|------|------|
| `check-quick.sh` | 日常快速质量门：Ruff、Python 测试、mypy、架构检查、API 漂移检查、前端 lint/typecheck/test、spec health。 |
| `check.sh` | 完整质量门（提交前）：quick gate + 前端测试覆盖率 + 生产构建。 |
| `check-ci.sh` | CI 质量门：完整 gate 的 CI 扩展版本。 |
| `run-local-gate.sh` | 对精确 commit 执行 quick/full gate，并在 Git common directory 记录可复用的本地通过凭证。 |

## 架构治理

| 脚本 | 用途 |
|------|------|
| `check_architecture.py` | 检查模块边界、路由响应模型、源文件体积预算。 |
| `check_invariants.py` | 校验 `config/architecture/architecture-invariants.yaml` 与 `architecture-exemptions.yaml`。 |
| `ratchet_architecture_budgets.py` | 更新 `config/architecture/architecture-budgets.json` 基线；拒绝抬高 ceiling。 |
| `generate_architecture.py` | 从代码 AST 自动生成 `docs/architecture/backend.md`、`frontend.md`、`pipeline.md`、`deployment.md` 的表格章节。 |

## Spec / Skill 治理

| 脚本 | 用途 |
|------|------|
| `verify_specs.py` | 检查 design specs 的引用健康，自动分类到 `specs/`、`completed/`、`archive/`，并生成 `SPEC_HEALTH.md`。 |
| `check-skills-shared.py` | 校验外部 Pi skill 仓库与项目共享引用文件的一致性。 |
| `migrate-skills-to-external-repos.py` | 将当前源码树中的 skill 迁移到外部 git 仓库。 |

## 迁移与工具

| 脚本 | 用途 |
|------|------|
| `finalize-workspace-executor-migration.py` | Phase 6 Workspace Executor 迁移最终化；`--check` 只读，`--apply` 执行迁移。 |
| `migrate-video-hive-to-agent-legion.py` | 从旧 Video Hive 运行时迁移数据到 Agent Legion Workspace Jobs。 |
| `generate-api-types.sh` | 导出后端 OpenAPI 并生成 `frontend/src/generated/api.ts`；`--check` 只做漂移检查。 |
| `export_openapi.py` | 不启动 Worker 导出 OpenAPI 模式。 |
| `install-git-hooks.sh` | 配置 worktree 兼容的版本化 pre-commit / pre-push 钩子。 |
| `check-pi.sh` | Pi CLI 环境 smoke 检查。 |

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
UV_CACHE_DIR=.uv-cache uv run python scripts/<script>.py
```
