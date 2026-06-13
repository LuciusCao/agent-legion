# Video Hive 架构风险 Review 报告

**日期**：2026-06-13  
**分支**：`codex/workspace-executor-governance`  
**Review 方式**：通过 4 个并行的只读 explore agent 分别审查后端、前端、质量门禁与测试、配置/数据流/部署；并在本地运行 `scripts/check-quick.sh` 相关命令验证当前基线。  
**当前基线**：后端 pytest 1147 passed / 89.72% 覆盖；前端 lint / typecheck / build / test:coverage 通过；`scripts/check_architecture.py` 通过。

---

## 1. 总体结论

Video Hive 当前是一个**功能成熟、架构门禁完善**的本地教育视频处理控制台。Phase 5/6 的 executor governance 规则已经在代码和门禁中落地，大部分明显越界（如 route 直接调用 Video Hive phase、service 导入 FastAPI、手写前端 transport 类型等）都能被 `scripts/check_architecture.py` 拦截。

但系统在以下方面仍存在显著风险：

- **职责边界**：部分 route/service/DB 层职责倒置，存在“GET 写库”“DB 层调 service”等反模式。
- **并发与资源生命周期**：executor pool 卡住会永久泄漏槽位；worker shutdown 无法优雅中断阻塞任务；删除恢复存在数据竞争窗口。
- **数据安全**：`storage_dir` 未做路径包含校验，可导致任意文件读写/删除；整个系统无认证层。
- **配置与部署**：密钥与 macOS 绝对路径直接写入 YAML；无 health/readiness 端点；无自动备份与 retention 策略。
- **前端架构**：`material-web` 类型全部声明为 `any`、store 混入 `ReactNode`、API 层无 timeout 与运行时校验。
- **测试与门禁**：29 条 SQLite 未关闭连接警告；部分关键路径（`pipelines/skills.py`、SenseVoice 真实路径）覆盖不足；前端无 coverage threshold。

**整体判断**：当前系统适合“受信任的本地单用户”场景；若要网络暴露、多用户协作或长期无人值守运行，需要完成 P0 与大部分 P1 项的加固。

---

## 2. 风险优先级总览

| 优先级 | 数量 | 核心主题 |
|--------|------|----------|
| **P0** | 8 | 安全、数据完整性、并发泄漏、配置密钥 |
| **P1** | 22 | 职责边界、可观测性、可维护性、测试覆盖、前端类型安全 |
| **P2** | 7 | 死代码、索引、迁移文档、打包体积、日志治理 |

---

## 3. P0 高风险项（必须尽快处理）

### 3.1 路径穿越可导致任意文件读写/删除

- **位置**：
  - `server/app/pipeline/common.py:7-13`
  - `server/app/jobs/queries.py:293-296`
  - `server/app/services/job_deletion.py:80-101`
- **问题**：`storage_dir` 直接取自数据库并转为 `Path`，没有任何 `relative_to(videos_dir/jobs_dir)` 的包含校验。`delete_video` 与 job deletion 会对其执行 `shutil.rmtree`。
- **风险**：恶意或损坏的 `storage_dir` 可导致读取、写入或删除服务器任意目录。
- **建议**：在所有基于 `storage_dir` 的文件操作前，强制校验其位于 `videos_dir` / `jobs_dir` 之内。

### 3.2 GET 接口写库并读盘，破坏幂等性与分层

- **位置**：`server/app/routes/videos.py:119-151`
- **问题**：`GET /api/videos/{id}` 会调用 `_enrich_video`、读取 `interactions.json` / `review_result.json`，然后调用 `db.update_video` 写入 `interaction_stats_json`。
- **风险**：
  - 读路径依赖磁盘 artifacts 是否存在/合法；
  - 并发读取时可能产生脏写或竞争；
  - 违反 REST 读语义与项目自身的 route/service 分层意图。
- **建议**：把 enrichment/caching 移到 service，GET 保持只读；缓存通过显式回填任务或监听阶段完成事件触发。

### 3.3 `JobRerunService` 中残留死代码，可绕过删除保护

- **位置**：`server/app/services/job_rerun.py:157-189`
- **问题**：`JobRerunService` 中仍有 `delete()` / `batch_delete()`，直接 `shutil.rmtree` 与 `Path.unlink`，绕过 `JobDeletionService` 的 staged-trash、lease guard、running-node 检查。
- **风险**：如果未来被重新暴露，会造成数据竞争与误删。
- **建议**：直接移除这两个方法，统一使用 `JobDeletionService`。

### 3.4 删除恢复可能覆盖并发新建的数据

- **位置**：`server/app/services/job_deletion.py:151-162`
- **问题**：恢复时若原目录已被重建（例如并发 worker 或其他请求），会先用 `shutil.rmtree` 删除新目录，再把 staged 副本移回。
- **风险**：明确的数据丢失窗口。
- **建议**：恢复前校验原路径 inode/mtime，或改用原子 rename / 写时复制策略。

### 3.5 卡住的 executor 调用会永久泄漏线程池槽位

- **位置**：
  - `server/app/executors/runtime.py:28-69`
  - `server/app/executors/openclaw.py:172-191`
  - `server/app/executors/pi.py:186-206`
- **问题**：`executor.execute(context)` 在池线程内同步阻塞，虽有子进程 timeout，但没有对 `ExecutionRuntime.run` 本身的 watchdog / future-level timeout。若子进程忽略信号或本地 handler 死锁，槽位永不归还。
- **风险**：单个异常 node 可饿死整个 executor pool，直到进程重启。
- **建议**：在 `PipelineWorkerThread` 层对 future 加硬超时，或给 `cancel()` 增加中断在 flight 调用的机制。

### 3.6 配置文件中明文存放密钥与绝对路径

- **位置**：`config/pipeline.yaml:17-36`
- **问题**：
  - `cms.token_gen.secret`、`cms.token` 明文存放；
  - 使用 macOS 专属绝对路径（`/opt/homebrew/...`、`/Users/user/...`）。
- **风险**：
  - 密钥易误提交；
  - 跨平台 / 容器部署直接失败；
  - 配置错误只能在视频入队运行后才能发现。
- **建议**：密钥从环境变量 / vault 加载；路径使用可配置 / 相对默认值；启动时校验 ASR / Pi / OpenClaw 资源存在性。

### 3.7 系统无认证层

- **位置**：`server/app/main.py:170-216`
- **问题**：FastAPI 未挂载任何 auth 中间件，批量删除、重跑、打包、工作区配置等端点完全开放。
- **风险**：一旦暴露到网络，任何人可操作队列、触发外部 agent 执行、删除数据。
- **建议**：至少增加 API key / bearer token 中间件，再考虑网络暴露。

### 3.8 V004 迁移是破坏性重建，中断可能损坏数据库

- **位置**：`server/app/db/migrations/v004_workspace_dag_foreign_keys.py:98-363`
- **问题**：创建新表、复制数据、`drop table`、重命名。WAL 模式下若进程中断，`-wal`/`-shm` 可能不一致；且该迁移假设 V006 列已存在于旧 `jobs` 表，对仅含 V001–V003 的旧 DB 会失败。
- **风险**：老旧 DB 打开时可能因中断或版本不匹配而损坏。
- **建议**：增加幂等性 / 修复检查；或改用非破坏性的 `ALTER TABLE` / `CREATE INDEX` 路径。

---

## 4. P1 中风险项

### 4.1 后端职责边界

| 问题 | 位置 | 建议 |
|------|------|------|
| DB 层依赖 service/presentation 层 | `server/app/db/queries.py:15-16` | 把 `_enrich_video`、`_backfill_interaction_stats` 上提到 service 或响应构建器 |
| Workspace stats 读取过时的 agent assignment | `server/app/services/workspace_configuration.py:234-251` | 改为从 `workspace_executor_allocations` 推导可用 executors |
| `continue_job` 可把 completed 改回 running | `server/app/services/job_execution.py:251-277` | 在 `resume_job` 中校验 job 是否已终态 |
| Lease 仓库不区分“容量满”与“配置错误” | `server/app/executors/leases.py:32-47` | 让 `try_claim` 返回明确状态或抛出可区分异常 |
| Lease 过期把整个 job 标为 failed | `server/app/executors/_lease_lifecycle.py:141-183` | 考虑仅把过期 node 标 stale，不直接判 job failed |
| Worker shutdown 不 cancel 阻塞任务 | `server/app/worker_thread.py:153-162` | shutdown 前 cancel futures，或加全局 watchdog |
| `OpenClawRunner` 在 artifact 目录清理硬编码文件名 | `server/app/pipeline/openclaw.py:188-191` | 把 agent workspace 隔离到临时目录 |
| `AgentStatusManager` 私有状态被外部直接修改 | `server/app/routes/videos.py:83`、`services/video_actions.py:83` | 提供公共 API 驱逐 stale busy 条目 |
| SSE 事件管理器驱逐客户端时未清理 per-video 订阅 | `server/app/events.py:20-25`、`46-53` | 从 `_video_clients` 同步移除被驱逐队列 |

### 4.2 后端错误处理与可观测性

| 问题 | 位置 | 建议 |
|------|------|------|
| 多处 `except Exception` 吞掉意外失败 | `services/job_execution.py`、`job_rerun.py`、`job_deletion.py` | 区分 SQLite / 磁盘 / 编程错误，暴露可重试语义 |
| 无 health / readiness 端点 | `server/app/main.py` | 增加 `/health` `/ready`，校验 ASR、Pi、DB |
| 无结构化指标与追踪 | 全局 | 增加 queue depth、phase duration、lease contention 指标 |
| SQLite 单写 + 短 busy_timeout | `server/app/db/connection.py:5-12` | 评估增加 busy timeout 或连接池 |
| 29 条未关闭 SQLite 连接警告 | pytest 输出 | 检查 `connect_sqlite` 与测试 fixtures 的关闭路径 |

### 4.3 配置与数据流

| 问题 | 位置 | 建议 |
|------|------|------|
| 无 retention / 自动清理 | `data/` 无策略 | 为 videos/packages/jobs/logs 增加保留策略与配额 |
| 日志无轮转与 PII 过滤 | `server/app/worker.py:191`、`pipeline/openclaw.py` | 增加轮转、保留期，强化敏感信息过滤 |
| CMS URL 与 token 硬编码 | `config/pipeline.yaml:26`、`server/app/cms/client.py` | 支持环境驱动覆盖与凭证轮换 |
| 下载不验证部分文件 | `server/app/pipeline/download.py:11-27` | 校验文件完整性或重新下载 |
| `upload_params.json` 状态硬编码为 `"3"` | `server/app/pipeline/upload_params.py:247-249` | 根据实际 review 结果映射状态 |

### 4.4 迁移与恢复

| 问题 | 位置 | 建议 |
|------|------|------|
| Finalizer 是单向且无回滚命令 | `scripts/finalize-workspace-executor-migration.py` | 增加 `--rollback` 与恢复文档 |
| App 启动依赖 finalizer | `server/app/main.py:102-123` | 把 finalizer 与常规启动解耦，避免迁移 bug 成为 outage |
| V005 依赖 SQLite ≥3.35 | `server/app/db/migrations/v005_remove_legacy_executor_paths.py:15-26` | 启动时检查 SQLite 版本或提供降级路径 |
| 迁移注册表跳过 V005 | `server/app/db/migrations/registry.py:14-20` | 明确文档化 V005 由 finalizer 负责的原因 |
| 无持续 SQLite 备份 | `server/app/executors/backup.py` | 增加定时/按需备份与恢复文档 |

### 4.5 前端架构

| 问题 | 位置 | 建议 |
|------|------|------|
| `material-web.d.ts` 全部声明为 `any` | `frontend/src/types/material-web.d.ts:6` | 使用官方 JSX 类型或逐个精确声明 |
| 手写类型与生成类型叠加 | `frontend/src/types.ts` | 尽量从 `generated/api.ts` 派生，减少手工覆盖 |
| `api.ts` 无 timeout、响应仅 `as T` | `frontend/src/api.ts:222` | 加 fetch timeout 与运行时 schema 校验 |
| `PackageHistoryDialog` 未绑定 `onClosed` | `frontend/src/components/PackageHistoryDialog.tsx:141` | 绑定 `onClosed` 同步 React state |
| `useDetailPage` 是“上帝 hook” | `frontend/src/hooks/useDetailPage.ts` | 按职责拆分为多个 focused hooks |
| Store 中混入 `ReactNode` | `frontend/src/stores/uiStore.ts` | 把 UI 占位逻辑从 store 移出 |
| 派生状态通过 subscribe 写回 store | `frontend/src/stores/videoStore.ts:313-350` | 改用 selector / memo |
| API 请求无 seq/cancel，快速切换易竞态覆盖 | `frontend/src/stores/artifactStore.ts`、`settingStore.ts` | 增加请求序列号或 AbortController |
| `WorkspaceJobList` 职责过重 | `frontend/src/views/WorkspaceJobList.tsx` | 拆分为列表、创建、过滤等子组件 |
| `InteractionOverlay` 分支过多 | `frontend/src/components/InteractionOverlay.tsx` | 按交互形态拆分子组件 |
| ESLint 规则偏弱 | `frontend/eslint.config.js` | 启用 `no-floating-promises`、`strict-boolean-expressions` 等 |

### 4.6 质量门禁与测试

| 问题 | 位置 | 建议 |
|------|------|------|
| `server/app/pipelines/skills.py` 0% 覆盖 | issue 028 | 补充路径穿越与缺失 contract 的测试 |
| `transcribe_sensevoice.py` 真实路径几乎未测 | `server/app/pipeline/transcribe_sensevoice.py` | 增加集成测试或 CI 用真实 funasr/ffmpeg 验证 |
| `server/app/executors/leases.py` 异常分支未覆盖 | `server/app/executors/leases.py` | 补充 lease 异常/回滚测试 |
| 前端无 coverage threshold | `frontend/vite.config.ts` | 增加 statements/branches 阈值 |
| React Router v7 future flags 警告 | 前端测试输出 | 在测试配置中设置 future flags |
| `routes/dependencies.py` 死代码 | `server/app/routes/dependencies.py` | 接入路由或删除 |
| `mypy` 不检查 `tests/` | `pyproject.toml:59` | 评估是否对测试代码也启用类型检查 |

---

## 5. P2 技术债与低风险改进

1. **死代码清理**
   - `server/app/pipelines/executor.py`（212 行，未引用）
   - `server/app/pipelines/artifacts.py::clear_rerun_outputs`
   - `server/app/pipelines/scheduler.py` 中仅死代码调用的 helper
   - `server/app/routes/packages.py` 中的 legacy `/package` 路径
   - `server/app/routes/dependencies.py`

2. **Schema 索引缺口**
   - `node_runs.status`、`videos.status` 缺少索引，规模大了会慢。

3. **前端打包**
   - `WorkspaceLayout` 同时静态与 lazy import 导致代码分割失效 warning；首包 436KB 可考虑进一步拆分。

4. **Pipeline 定义校验**
   - `server/app/pipelines/definition.py` 不验证 `capability` 是否被任何 Executor 实现，错误只能到 claim 时才暴露。
   - Workspace executor binding 也未在 route 层做 capability 兼容性校验。

5. **Pipeline 版本化**
   - `config/pipelines/*.yaml` 无 version/checksum，修改后立即影响新 job，旧 job 语义不一致。

6. **日志治理**
   - 无轮转、无保留期、`_sanitize_log` 正则易绕过。

7. **架构门禁的小缺口**
   - 固定名称列表检测 DAG traversal，新增 helper（如 `reachable_nodes`）可能漏检；
   - DDL 检测跳过 f-string；
   - 动态 import 可绕过 import visitor。

---

## 6. 推荐处理顺序

### 立即（1–2 周内）
1. 为所有 `storage_dir` 操作增加路径包含校验。
2. 移除 `routes/videos.py` GET 中的写库与磁盘 backfill。
3. 删除 `JobRerunService` 中的 `delete` / `batch_delete` 死代码。
4. 修复 `job_deletion.py` 恢复时的竞争窗口。
5. 为 executor 飞行调用增加 watchdog / future timeout。
6. 将 CMS 密钥移出 YAML；修复绝对路径配置。
7. 增加认证中间件（至少 API key）。
8. 为 V004/V005 迁移增加更安全的幂等路径或版本检查。

### 近期（1 个月内）
1. 修复 `material-web.d.ts` 的 `any`、前端 API timeout、SSE 竞态、`PackageHistoryDialog` onClosed。
2. 拆分 `useDetailPage`、`WorkspaceJobList`、`InteractionOverlay`。
3. 修复 DB 层对 service 的反向依赖、workspace stats 的数据源。
4. 处理 pytest 的 29 条 SQLite 连接警告。
5. 补充 `pipelines/skills.py`、SenseVoice 真实路径、lease 异常分支的测试。
6. 增加 `/health` `/ready` 端点与基础指标。
7. 制定 videos/packages/jobs/logs 的 retention 策略。
8. 建立 SQLite 备份与恢复机制。

### 中期（2–3 个月内）
1. 清理死代码模块。
2. 优化 `node_runs.status` / `videos.status` 索引。
3. 为 pipeline YAML 增加 capability 运行时校验与版本字段。
4. 前端代码进一步拆分与 ESLint 规则收紧。
5. 日志轮转、PII 过滤、结构化日志。
6. 评估是否需要从 SQLite 迁移到支持多写的数据库，以支撑更高并发。

---

## 7. 附录：当前 Quality Gate 运行结果

| 命令 | 结果 |
|------|------|
| `uv run pytest -q --cov=server --cov-report=term-missing` | 1147 passed, 89.72% 覆盖，29 条 ResourceWarning |
| `uv run python scripts/check_architecture.py` | pass |
| `uv run python scripts/verify_specs.py --check` | pass |
| `uv run ruff check .` / `ruff format --check .` | pass |
| `uv run mypy server/app` | pass |
| `npm run api:check` | pass |
| `npm run lint` / `typecheck` / `format:check` / `build` | pass |
| `npm run test:coverage` | 79.77% statements，无 threshold |

---

## 8. 相关 Open Issues

以下已存在的 issue 与本报告发现高度相关：

- `issues/open/008-P2-backend-data-layer-edge-cases.md`：`.env` 解析、`assemble.py` 空字幕、`_broadcast` 静默失败、日志分页。
- `issues/open/011-P2-testing-and-architecture-debt.md`：`AgentStatusManager` 职责混杂、`Database` 类耦合事件通知。
- `issues/open/028-P1-test-coverage-gaps.md`：`pipelines/skills.py` 0% 覆盖、`routes/packages.py` 覆盖不足。
- `issues/open/035-P2-cors-middleware-config.md`：生产前端 API target / CORS 未解决。
