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
| `check_coverage_partitions.py` | 按分区（关键模块/目录）报告覆盖率下限，防止关键模块被全局平均掩盖；默认 report 模式，`--enforce` / `AGENT_LEGION_COV_PARTITIONS=enforce` 转阻塞。 |
| `pytest_gate_shard.py` | pytest 插件：`GATE_SHARD=i/n` 按 md5(nodeid) 对收集结果确定性分片（CI postgres tier 三个 shard 用）。 |
| `pytest_telemetry.py` | pytest 插件：把 rerun 尝试记录为 JSON 报告，供 CI 遥测与 flaky 治理。 |
| `summarize_test_results.py` | 把 JUnit 与 rerun 报告渲染为 Markdown 汇总（写 CI job summary；stdlib-only）。 |
| `check_reruns.py` | flaky 治理：rerun 命中未登记 nodeid 或登记条目超期时失败（registry 在 `tests/flaky_registry.yaml`；nightly/ci-extended 用）。 |

## 架构治理

| 脚本 | 用途 |
|------|------|
| `check_architecture.py` | 检查模块边界、路由响应模型、源文件体积预算。 |
| `check_invariants.py` | 校验 `config/architecture/architecture-invariants.yaml` 与 `architecture-exemptions.yaml`。 |
| `ratchet_architecture_budgets.py` | 更新 `config/architecture/architecture-budgets.json` 基线；拒绝抬高 ceiling。 |
| `generate_architecture.py` | 从代码 AST 自动生成 `docs/architecture/backend.md`、`frontend.md`、`deployment.md` 的表格章节。 |
| `generate_architecture_frontend.py` | `generate_architecture.py` 的前端路由提取 helper。 |
| `check_exemption_age.py` | 提醒移除条件已过期的架构豁免（非阻塞；check.sh / CI 调用）。 |

## Agent Worker 子系统

Worker 执行进程、Worker Service、Supervisor、配置存储与 CLI 已迁至顶层 `worker/` 包
（`worker/executor.py`、`worker/service.py`、`worker/supervisor.py`、`worker/config_store.py`、
`worker/client.py`、`worker/cli.py`、`worker/cleanup.py`），控制台静态资源在 `worker/ui/`。

## Spec / Skill 治理

| 脚本 | 用途 |
|------|------|
| `verify_specs.py` | 检查 design specs 的引用健康，自动分类到 `specs/`、`completed/`、`archive/`，并生成 `SPEC_HEALTH.md`。 |
| `check-skills-shared.py` | 校验外部 Pi skill 仓库与项目共享引用文件的一致性。 |

## 示例 workflow

| 脚本 | 用途 |
|------|------|
| `import-demo.sh` | 导入示例 workflow（`education_video_problems_generation`）的 4 个示例 skill：复制 `examples/skills/*` 到本机 skill 源目录并逐目录 `git init` + 初始 commit + 打 tag `v1.0.0`（幂等，已有 tag 的 skill 跳过不覆盖）。由 `make import-demo` 调用；测试可用 `AGENT_LEGION_DEMO_SKILLS_DIR` 覆盖目标根目录。 |

## 迁移与工具

| 脚本 | 用途 |
|------|------|
| `generate-api-types.sh` | 导出后端 OpenAPI 并生成 `frontend/src/generated/api.ts`；`--check` 只做漂移检查。 |
| `export_openapi.py` | 不启动 Worker 导出 OpenAPI 模式。 |
| `install-git-hooks.sh` | 配置 worktree 兼容的版本化 pre-commit / pre-push 钩子。 |
| `check-pi.sh` | Pi CLI 环境 smoke 检查。 |
| `init-worktree.sh` | 一键初始化新 worktree（复制 .env、派生并创建专属 Postgres 库、生成 deploy/secrets、种子 worker 配置、恢复 workspace 调度；幂等，macOS）。 |
| `dev_stack.sh` | 开发环境一键启停（`make dev-up` / `dev-down` / `dev-status`）：后台编排 backend + frontend + worker（复用 Makefile `dev-*` target），幂等，日志在 `data/logs/dev-*.log`，up 完成后打印各服务 URL。 |
| `native-prod-up.sh` / `native-prod-down.sh` | 启停原生（非 Docker）生产环境（后端 8000 + worker 8787，前端由后端直接服务 `frontend/dist`；幂等，仅 prod worktree 使用）。由 `make prod-up` / `make prod-down` 调用。 |
| `stack-prod-up.sh` | 一键启动本地 Docker 生产 stack（PostgreSQL + Host + Worker）：secrets 预检、postgres 健康断言、ASR 模型预热、全 stack 健康等待（仅 prod worktree 使用）。由 `make prod-up docker` 调用，停止用 `make prod-down docker`。 |
| `seed_from_prod.py` | 从本地 prod Docker stack 的 Postgres 只读导出并种子 develop 库（目标库名为 prod 名或 host 非 loopback 时拒绝执行）。无 make target，直接 `uv run python scripts/seed_from_prod.py` 调用。 |
| `gc_artifacts.py` | 报告/回收 content-addressed artifact store 中零引用且超过在途宽限期的孤儿 blob（默认 dry-run，`--apply` 回收）。 |

## 一次性与运维脚本

直接操作生产表的脚本必须先 `--dry-run` 验证影响面，再正式执行；退役后删除并在本节留档。

| 脚本 | 用途 | 退役条件 |
|------|------|----------|
| `backfill_failure_classification.py` | 为历史 failed `node_runs` 回填 `failure_category`/`failure_detail`（幂等，支持 `--dry-run` / `--include-unknown`）。 | 生产库中无未分类的 failed 行，且分类规则稳定不再需要重评 `unknown`。 |
| `backfill_worker_output_validation.py` | 补跑 EXEC-VALIDATION-001 之前 Worker 完成节点的输出校验，失败的标记节点/任务 failed（幂等，支持 `--dry-run`）。 | 所有存量 Worker-run 节点完成重校验（无候选行）。 |
| `view-session.py` | 将 OpenClaw session JSONL 渲染为人类可读的对话日志。 | OpenClaw runner 退役或控制台内置 session 查看能力。 |
| `velites_diff_events.py` | 结构对比 Node Pi 与 velites 的 `events.jsonl` 事件流（忽略时序字段与 delta 事件差异）。 | velites 完全替代 Node Pi 且回归基线归档后。 |
| `migrate_job_dirs_to_shards.py` | 一次性迁移：把扁平 `jobs/<workspace>/<job_id>` 目录改名为分片布局并同步 `jobs.storage_dir`（幂等可重入，`--apply` 需停后端/worker）。 | 生产库不再有 3 段 legacy `storage_dir` 行（全部迁移到 4 段分片布局）。 |
| `velites_replay.py` | velites 灰度 Phase 1 影子回放：抽样生产 run 目录，离线双跑 Node Pi 与 velites 并对比事件流与声明输出（只读生产数据，不碰 DB）。 | velites 灰度完成、影子回放基线归档后。 |

一次性脚本（`diagnose_cms.py`、`cleanup-agent-pollution.py`、`backfill-node-run-dirs.py`、`archive/backfill_source_uuid.py`）已于 2026-07-22 退役删除；一次性迁移脚本（`import-sqlite-to-postgres.py` + `sqlite_import_support.py`、`migrate-config-layout.py`）已于 2026-07-23 随 SQLite→PostgreSQL 迁移与配置布局拆分完成退役删除；历史用法见 git 历史。

## 子目录

| 目录 | 用途 |
|------|------|
| `architecture/` | `check_architecture.py` / `ratchet_architecture_budgets.py` 的检查实现（预算盘点、边界、路由契约、import 环等按检查域划分的模块）。 |
| `quality/` | 架构不变量与豁免注册表的加载/校验实现（`invariants.py`、`exemptions.py`、`exemption_age.py`），供 `check_invariants.py` / `check_exemption_age.py` 与 `architecture/file_budgets.py` 使用。 |
| `git-hooks/` | 版本化的 pre-commit / pre-push 钩子 dispatcher，由 `install-git-hooks.sh` 安装到 Git common directory，再转发到 worktree 根的 `.githooks/`。 |
| `remote/` | 远程 LLM 网关（`llm_gateway.py` 及 HTTP/SSE/stream/config 模块），见 `docs/remote-execution-runbook.md`。 |
| `stress/` | 压力测试：`simulate_agents.py` 合成负载生成器、`run_e2e_stress.py` 端到端压测 runner。 |
| `e2e/` | 浏览器 smoke E2E：`run_browser_smoke.py`（确定性 Chromium 冒烟，CI e2e-smoke / nightly-e2e job 调用）与数据库 helper `_database.py`。 |
| `seed/` | workflow 种子包导出/导入工具（`export_seed.py` / `import_seed.py` / `seed_common.py`）：把 workflow 定义（DAG、Agent、节点代码、skill 源锁定）在实例间迁移，幂等；平台级通用工具，业务种子包留在私有侧。详见 `scripts/seed/README.md`。 |

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
