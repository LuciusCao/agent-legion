# Agent Legion 测试架构优化计划

状态：Phase 1 complete; Phase 2 pending
分支：`test/test-architecture-optimization`
基线：`develop@836235b9`
日期：2026-08-01

## 1. 背景

当前仓库已经具备较完整的测试体系：Python 使用 pytest、pytest-xdist 和
pytest-cov，前端使用 Vitest、Testing Library 和 Playwright，`velites` 使用 Rust
原生测试；CI 还区分普通门禁、`full_gate` 和 nightly `ci_extended`。

现有体系的主要问题不是“没有测试”，而是测试边界、执行成本和覆盖率门禁之间没有
完全对齐：

1. 根 `tests/conftest.py` 在导入及 autouse fixture 中绑定 PostgreSQL，纯单元测试也
   无法脱离数据库收集和执行。
2. 后端普通测试在基线提交的 develop CI 中执行 2327 个用例，耗时 430.45 秒；32 个
   `full_gate` 用例另耗时 16.38 秒。
3. 前端 137 个测试文件、1058 个用例，本机带覆盖率耗时 57.03 秒，但同一提交在
   GitHub Actions 上耗时 368.11 秒。Vitest 报告中的实际测试体约 39 秒，模块导入和
   jsdom 环境初始化占据大量累计时间。
4. 后端综合覆盖率达到 92.86%，前端 lines 达到 88.42%，但聚合门槛允许关键模块低于
   60%，且前端部分生产入口没有进入 coverage 数据集。
5. Playwright 目前只有工作区压力场景，没有进入 PR/push 或 nightly CI，也缺少短小的
   真实浏览器用户流程测试。
6. CI 没有长期保留测试耗时、JUnit、rerun 和 Playwright trace，难以发现慢化趋势与
   flaky 测试。

## 2. 基线数据

以下数据作为优化前基线。开始代码修改前，应在新 worktree 中重新采样并保存机器、
worker 数、是否启用 coverage 等上下文。

| 测试车道 | 规模 | 当前耗时 | 当前覆盖率 |
| --- | ---: | ---: | ---: |
| Python quick/full（CI 普通层） | 2327 passed，1 skipped | 430.45s | 92.54% |
| Python `tests/full` | 32 passed | 16.38s | 合并后 92.86% |
| Frontend Vitest，本机无 coverage | 137 files / 1058 tests | 50.18s | 不适用 |
| Frontend Vitest，本机有 coverage | 137 files / 1058 tests | 57.03s | lines 88.42% |
| Frontend Vitest，GitHub CI | 137 files / 1058 tests | 368.11s | lines 88.42% |
| Rust `cargo test`，GitHub CI | 约 80 tests | 约 13s | 未配置源码覆盖率 |

前端覆盖率基线：statements 85.95%、branches 76.94%、functions 84.26%、lines
88.42%。

后端当前低覆盖模块包括：

| 模块 | 行覆盖率 |
| --- | ---: |
| `server/app/services/transcription_providers.py` | 28% |
| `server/app/agent_artifacts.py` | 38% |
| `server/app/workflows/skill_version_fallbacks.py` | 39% |
| `server/app/agent_dispatch.py` | 54% |
| `server/app/routes/job_workflow_upgrade.py` | 57% |
| `server/app/agent_dispatch_pool.py` | 58% |
| `server/app/services/job_log_raw.py` | 67% |

前端当前重点盲区包括：

- `src/api` 整体 lines 约 46.61%，多个 transport wrapper 为 0%。
- `WorkersSection.tsx` 为 0%。
- `UsersAdminPage.tsx` 约 61.9%。
- `JobDetailPage.tsx` 约 68.18%。
- workflow upgrade action hook 约 12.5%。
- `LoginPage.tsx`、`SetupPage.tsx`、`App.tsx`、`main.tsx` 未进入 coverage 数据。

## 3. 目标与非目标

### 3.1 目标

1. 纯 Python 单元测试可以在没有 PostgreSQL 的机器上完成 collection 和执行。
2. 需要数据库的测试继续保持每个 xdist worker 独立 schema、每测试数据隔离，以及
   `fresh_schema` 的 DDL 安全语义。
3. 在不减少测试有效性的前提下，将 develop CI 的关键路径从约 9 分钟降到 6 分钟内。
4. 将 Python 普通测试车道降到 250 秒内，将前端 Vitest CI 降到 150 秒内。
5. coverage 的分母显式覆盖所有应测生产文件，关键模块不能依赖其他高覆盖模块“平均
   过线”。
6. PR 门禁拥有少量确定性真实浏览器 E2E，nightly 执行压力和扩展浏览器场景。
7. CI 能回答“哪个文件最慢”“哪个用例发生 rerun”“哪个模块覆盖率下降”。

所有耗时目标以连续三次 CI 的中位数验收；单次 hosted runner 抖动不直接判定回归。

### 3.2 非目标

- 不为了提速删除业务断言或降低现有总体覆盖率门槛。
- 不将所有集成测试改成 mock；数据库、进程、SSE、沙箱和并发语义仍需真实边界证据。
- 不在本计划中改变生产数据库模型、业务 API 或 executor 调度行为。
- 不要求 PR 门禁运行五分钟压力测试或真实外部 CMS/LLM。
- 不立即引入 Rust coverage 门禁；先保留现有 contract、sandbox 和 integration 测试。

## 4. 目标测试分层

### 4.1 Python

| 层级 | 目的 | 外部依赖 | 默认门禁 |
| --- | --- | --- | --- |
| unit | 纯函数、配置、解析、调度决策、架构规则 | 无 PostgreSQL、无网络 | 本地 fast/smoke、CI |
| integration | route/service/query、真实 PostgreSQL、文件与子进程边界 | PostgreSQL；必要时本地进程 | CI quick |
| full | 跨控制面、并发、恢复、安全场景 | PostgreSQL、velites/bwrap | PR/push full gate |
| extended | 重复压力、长时间竞态 | 专用 CI 环境 | nightly/manual |

数据库依赖采用显式 `postgres` marker/fixture，不再采用对全套测试无条件执行的数据库
autouse fixture。`client`、`job_db` 等高层 fixture 应通过依赖链自动请求数据库；直接构造
query/service 的测试需显式标记。

### 4.2 Frontend

| 层级 | 环境 | 典型内容 |
| --- | --- | --- |
| logic | Node | formatter、selector、store 纯逻辑、API 请求构造、DAG 算法 |
| component | jsdom | React component、hook、router/store 集成 |
| browser-smoke | Chromium | 登录、工作区、job 生命周期、workflow 操作 |
| browser-extended | Chromium + 可选 Firefox/WebKit | 压力、性能、兼容性 |

Coverage 必须合并 logic/component 结果，并显式定义生产源码 include/exclude。

## 5. 分阶段实施

### Phase 0：建立可重复基线与测试遥测

目的：先获得可信数据，避免凭主观感受优化。

任务：

- [x] 新增统一的测试计时说明或脚本，记录 commit、平台、CPU、worker 数、coverage 模式。
- [x] Python CI 添加 `--durations=30` 和 JUnit XML。
- [x] Vitest 输出 JUnit/JSON 结果，并保留 coverage summary。
- [x] CI job summary 保留聚合结果；原始 JUnit、Vitest JSON 和 HTML coverage 仅在临时
      runner 中使用，不上传可能包含私有源码或失败上下文的 artifacts。
- [x] 统计 rerun 次数并在 job summary 中展示。
- [x] 连续运行三次基线，记录中位数及最慢 30 个 Python 测试/前端文件。

验收：

- CI 日志能定位慢测试和 rerun，job summary 能查看聚合数量与执行环境。
- 遥测本身不使任一车道耗时增加超过 5%。
- 不改变现有测试选择范围和 coverage 门槛。

回滚：仅移除 reporter、job summary 和参数，不涉及测试实现。

Phase 0 本地验证记录（2026-08-01，Darwin arm64，10 logical CPUs）：

- 定向遥测与 gate-script 测试：10 passed，9.51s。
- pytest telemetry 在 xdist controller 下实跑：3 passed，3.43s，生成一份 JUnit 和一份
  `attempts=0` rerun 报告。
- Vitest reporter 实跑：137 files / 1058 tests 全部通过，JUnit 统计 1058 passed，JSON
  reporter 与 JUnit reporter 均成功落盘；新 worktree 冷缓存耗时 121.93s。
- 三 lane 首次 quick gate：frontend 1058 passed，Rust 全部通过；backend 在高负载下出现
  `test_local_executor_cancel_during_run` 时序 flaky。该用例隔离复跑 3/3 通过。
- backend-only、无 `GATE_LANES` 污染的最终 test phase：2334 passed，286.18s。
Phase 0 GitHub Actions 基线（2026-08-02，同一提交 `f01b248a`，均通过）：

| Run | backend job | frontend job | ci-extended | Rust | Python test | Vitest |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [30728743402](https://github.com/LuciusCao/agent-legion/actions/runs/30728743402) | 9m12s | 8m13s | 2m04s | 52s | 435.94s | 292.21s |
| [30729031946](https://github.com/LuciusCao/agent-legion/actions/runs/30729031946) | 9m10s | 9m25s | 1m16s | 49s | 443.34s | 399.74s |
| [30729326941](https://github.com/LuciusCao/agent-legion/actions/runs/30729326941) | 6m53s | 9m01s | 1m21s | 49s | 277.76s | 377.91s |
| 中位数 | 9m10s | 9m01s | 1m21s | 49s | 435.94s | 377.91s |

- 三次关键路径中位数为 9m12s；backend 与 frontend 共同决定关键路径。
- Python 2333 passed / 1 skipped，Vitest 137 files / 1058 tests，三次均未观察到 rerun。
- 相对既有 Python 430.45s、Vitest 368.11s 基线，测试级中位数分别增加约 1.3% 和
  2.7%，低于 5% 遥测开销上限。
- 第三次 Python 普通测试最慢 30 项如下；57.35s 的 session/setup 成本是首要优化对象：

| 秒 | 阶段 | 测试 |
| ---: | --- | --- |
| 57.35 | setup | `test_budget_exhaustion_marks_agent_end` |
| 3.22 | call | `test_app_startup_preserves_local_executor_configuration_for_workspace` |
| 2.73 | call | `test_build_openapi_schema_is_deterministic_and_portable` |
| 2.52 | call | `test_local_executor_without_timeout_completes` |
| 2.29 | call | `test_rerun_node_mark_for_rerun_value_error` |
| 2.18 | call | `test_workspace_agent_routes_are_absent_from_openapi` |
| 1.85 | call | `test_rerun_node_rejects_running_job` |
| 1.77 | call | `test_delete_job_response_model_is_exposed_in_openapi` |
| 1.66 | call | `test_batch_rerun_request_order_preserved` |
| 1.65 | call | `test_job_intake_handles_large_batch_across_default_chunks` |
| 1.64 | call | `test_workspace_settings_returns_resource_config` |
| 1.59 | call | `test_batch_rerun_node_not_found_for_one_job` |
| 1.56 | call | `test_get_job_run_log_returns_404_for_missing_run` |
| 1.56 | call | `test_workspace_batch_rerun_marks_jobs_queued` |
| 1.51 | call | `test_get_video_job_source_serves_local_source_mp4` |
| 1.44 | call | `test_workspace_configuration_rejects_invalid_binding_without_partial_update` |
| 1.43 | setup | `test_validate_srt_entry_within_limit_passes` |
| 1.43 | setup | `test_websocket_requires_session` |
| 1.43 | call | `test_get_job_detail_and_artifact_when_enabled` |
| 1.42 | call | `test_run_to_rejects_start_outside_target_closure` |
| 1.36 | call | `test_get_artifact_returns_404` |
| 1.35 | call | `test_get_job_run_log_rejects_escape` |
| 1.32 | setup | `test_validate_srt_entry_too_long_fails` |
| 1.30 | call | `test_job_detail_includes_node_inputs_outputs` |
| 1.29 | call | `test_executor_stats_available_respects_global_usage_by_other_workspaces` |
| 1.29 | call | `test_workspace_job_route_manifest` |
| 1.28 | call | `test_rerun_node_cleanup_failed` |
| 1.28 | call | `test_list_workspace_runs_filters_by_status_and_node` |
| 1.28 | call | `test_rerun_node_rollback_on_db_failure` |
| 1.27 | call | `test_batch_rerun_from_failed_node` |

- 前端原始 JSON 按安全策略不上传；以下最慢 30 个文件来自 Phase 0 本地冷缓存 JSON
  （121.93s 总耗时），用于模块排序，CI 仅保留 3 次聚合中位数：

| 秒 | 文件 | 秒 | 文件 |
| ---: | --- | ---: | --- |
| 7.53 | `SettingsPage.test.tsx` | 1.30 | `JobLogDialog.test.tsx` |
| 5.90 | `WorkspaceMainPage.test.tsx` | 1.28 | `JobRerunDialog.test.tsx` |
| 3.64 | `JobDetailPage.test.tsx` | 1.12 | `QuestionContentPanel.regression.test.tsx` |
| 3.15 | `WorkflowStudioPage.test.tsx` | 1.08 | `JobProgressPanel.test.tsx` |
| 3.13 | `AddDialog.test.tsx` | 1.06 | `JobDetailActions.test.tsx` |
| 2.95 | `InteractionOverlay.test.tsx` | 1.03 | `JobRerunDialog.failureCategory.test.tsx` |
| 1.95 | `SettingsComponents.test.tsx` | 0.98 | `useWorkflowStudio.test.ts` |
| 1.86 | `JobFilterBar.test.tsx` | 0.86 | `TokenUsageDialog.test.tsx` |
| 1.65 | `TokenUsagePanel.test.tsx` | 0.86 | `DeleteWorkspaceDialog.test.tsx` |
| 1.60 | `QuestionContentPanel.test.tsx` | 0.86 | `DagFullscreenDialog.test.tsx` |
| 1.60 | `JobActionBar.test.tsx` | 0.86 | `AgentStatusIndicator.test.tsx` |
| 1.47 | `WorkflowPublishReviewDialog.test.tsx` | 0.83 | `useAsync.test.ts` |
| 1.36 | `MonitoringPanel.test.tsx` | 0.82 | `SchemaConfigForm.test.tsx` |
| 0.79 | `DagGraph.test.tsx` | 0.79 | `ExecutorAllocationSection.test.tsx` |
| 0.77 | `VideoContentPanel.test.tsx` | 0.77 | `TokenUsagePage.test.tsx` |

### Phase 1：解除 Python 单元测试的全局 PostgreSQL 依赖

目的：形成真正可独立运行的 unit 层，并减少无意义的 schema 清理。

任务：

- [x] 将 conftest 模块导入阶段的 schema 创建移动到惰性 session fixture。
- [x] 引入 `postgres` marker，并提供明确的数据库隔离 fixture。
- [x] 非数据库测试不请求 `_session_test_schema`，不执行 TRUNCATE、连接池关闭或 agent
      definition 同步。
- [x] `client`、`job_db` 和数据库 query fixture 自动依赖 `postgres` 隔离。
- [x] 盘点直接访问数据库但未通过 fixture 的测试，逐文件显式标记。
- [x] 保留 `fresh_schema`：DDL 测试执行前后重建 schema，不能退化为普通 TRUNCATE。
- [x] 增加一个无 PostgreSQL collection/unit gate，证明纯测试不会意外连库。
- [x] 将 smoke 成员从易遗漏的单一文件白名单演进为“稳定 marker + 分层路径”，同时保留
      90 秒预算。

建议命令形态：

```bash
uv run pytest -q -m "not postgres and not full_gate and not ci_extended"
uv run pytest -q -m "postgres and not full_gate and not ci_extended" -n 4
```

验收：

- PostgreSQL 停止时，unit collection 和 unit suite 通过。
- integration/full 测试继续使用独立测试库和 per-worker schema。
- 数据隔离、并行执行、`fresh_schema`、连接池清理相关现有测试全部通过。
- 测试总数与原基线一致；任何减少都必须有明确的重分类说明。
- Python 普通测试 CI 中位数相对基线至少下降 25%。

风险与缓解：

- 漏标数据库测试可能污染其他用例：先引入 marker 审计/失败提示，再移除全局 autouse。
- xdist 下 fixture 顺序变化：为每个 worker schema、并发 TRUNCATE 和 fresh-schema 恢复增加
  定向回归测试。
- 直接 import 时读取环境变量：将环境隔离 fixture 与数据库 fixture 分离，凭据清理仍保留
  autouse。

回滚：恢复数据库 fixture 的 autouse 调用；marker 和遥测可以保留。

Phase 1 本地验证记录（2026-08-02，Darwin arm64，10 logical CPUs）：

- 使用不可达的 `127.0.0.1:1` 数据库 URL，初始离线 unit 层 1421 passed；两次带 coverage
  的 wrapper 用时 23.23s / 39.21s，证明 collection 与执行均不会连接 PostgreSQL。首次
  建库竞态修复增加 2 个回归用例后，最终 unit 层为 1423 passed。
- PostgreSQL integration 层 918 passed；无 coverage 用时 55.15s，两次带 coverage
  用时 118.32s / 139.81s。
- `tests/full -m full_gate` 32 passed（最终 10.04s）；三层最终合并覆盖率 92.88%，高于
  85% 门槛，新拆分的 executor registry factory 覆盖率为 100%。
- 最终三层合计 2373 passed，与普通层 2341 加 full 层 32 的重新分层一致；有效采样中
  未观察到 rerun。
- 增加竞态回归前的完整普通后端门禁 2339 passed / 73.33s，相比 Phase 0 本机 286.18s
  下降约 74%；后端、
  前端和 Rust 的跨语言 quick gate 也全部通过（228s）。
- 架构检查、生成文档检查、ruff 与定向 gate contract 测试通过；`main.py` 的执行器注册
  构建被拆出后从 250 行降至 227 行，并移除了原 246 行文件预算豁免。

Phase 1 GitHub Actions 验收（2026-08-02，提交 `cded03f8`，三次均通过）：

| Run | unit | PostgreSQL | full | Python 合计 | backend job | frontend tests | workflow |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [30740019516](https://github.com/LuciusCao/agent-legion/actions/runs/30740019516) | 100s | 255s | 18s | 373s | 7m43s | 385s | 9m16s |
| [30740360208](https://github.com/LuciusCao/agent-legion/actions/runs/30740360208) | 90s | 215s | 15s | 320s | 7m22s | 381s | 9m13s |
| [30740692455](https://github.com/LuciusCao/agent-legion/actions/runs/30740692455) | 83s | 198s | 15s | 296s | 7m03s | 365s | 8m51s |
| 中位数 | 90s | 215s | 15s | 320s | 7m22s | 381s | 9m13s |

- Python 三层合计中位数由 Phase 0 的 435.94s 降至 320s，下降 26.6%，通过至少 25% 的
  Phase 1 验收线；最终 combined coverage 为 92.86%，高于 85% 门槛。
- 首次远端运行
  [30734593605](https://github.com/LuciusCao/agent-legion/actions/runs/30734593605)
  暴露 xdist worker 并发首次建库竞态：多个 worker 同时执行 CREATE DATABASE，产生 154
  个 setup error。`383897dc` 使用 session advisory lock 串行化 catalog check/create，
  `cded03f8` 隔离 gate-script 子进程环境；全新临时数据库 4-worker 回归 6 passed，完整
  PostgreSQL 层 918 passed。
- backend job 中位数由 9m10s 降至 7m22s，但 workflow 关键路径中位数仍为 9m13s；当前
  瓶颈已经转移到 frontend tests（中位数 381s），由 Phase 2 继续处理。

### Phase 2：前端 Node/jsdom 分层与执行优化

目的：让纯逻辑测试不承担 jsdom 和完整 React setup 成本。

任务：

- [ ] 盘点当前 57 个 `.test.ts`，确认至少 46 个不依赖 Testing Library 的候选文件。
- [ ] 使用 Vitest projects 或文件约定拆分 Node 与 jsdom 环境。
- [ ] Node 项目只加载必要 setup；jsdom 项目保留 DOM matcher、EventSource、observer mocks。
- [ ] 在 setup 中补齐受控的 navigation 和 `HTMLMediaElement.play/pause` mock，清除当前通过
      测试中的 jsdom error 噪声。
- [ ] 测量 threads/forks、worker 上限和 GitHub runner 核数的组合，不盲目使用最大并发。
- [ ] 如单 job 仍超过目标，将 logic/component 拆成两个 CI shard，最终合并 coverage。
- [ ] 保持组件测试用户行为语义，不用大范围 shallow rendering 换取速度。

验收：

- 1058 个现有用例全部保留并通过。
- 测试日志不再出现未预期的 navigation/media “Not implemented” 错误。
- 本机无 coverage 中位数不高于 35 秒；GitHub CI Vitest 中位数不高于 150 秒。
- 合并后的 coverage 不低于原门槛。

回滚：projects/shard 可退回单 Vitest config；测试文件内容不需要回退。

### Phase 3：修正 coverage 分母并补关键盲区

目的：覆盖率反映真实生产源码风险，而不是只计算被测试导入的模块。

任务：

- [ ] 前端 coverage 显式 include `src/**/*.{ts,tsx}`。
- [ ] 明确排除 `.d.ts`、生成代码、测试 support；对 `main.tsx` 等薄启动文件记录是否排除及
      理由。
- [ ] 增加 coverage inventory 测试，防止新的生产文件悄悄不进入分母。
- [ ] 对关键目录设置分区门槛或 changed-lines 门槛；保留现有全局门槛。
- [ ] 优先补后端 agent dispatch/pool、workflow upgrade、transcription provider、artifact、raw
      log 和 skill fallback 的异常/取消/超时/资源不足分支。
- [ ] 优先补前端 login/setup、worker management、workflow upgrade、job detail 和关键 API
      transport 的契约与错误处理。
- [ ] 每个补测提交只覆盖一个业务簇，避免“大覆盖率提交”难以评审。

关键模块最低目标：

- agent dispatch、workflow upgrade、auth/bootstrap：lines 不低于 80%，关键错误分支必须有
  行为断言。
- 其他当前低于 60% 的生产模块：先提升至 70%，再根据复杂度提高。
- 新增/修改生产代码：changed lines 目标不低于 90%。

验收：

- 显式 include 后不存在无说明的生产源码漏计。
- 关键模块达到上述门槛，且测试验证外部可观察行为，不仅调用私有函数。
- 不通过排除文件、`pragma: no cover` 或删除断言维持总覆盖率。

回滚：分区/changed-lines 门槛可先改为非阻塞报告，但显式 coverage inventory 不回滚。

### Phase 4：加入短 E2E 与 nightly 浏览器压力测试

目的：覆盖 jsdom 无法验证的路由、浏览器 API、SSE 和前后端集成。

任务：

- [ ] 新建独立 `frontend/e2e/`，与现有五分钟 `stress/` 分开。
- [ ] 建立确定性 fixture/seed API，避免 E2E 依赖真实 CMS、LLM 或互联网。
- [ ] PR/push Chromium smoke 覆盖：bootstrap/login、创建工作区、创建/查看 job、rerun 或
      workflow upgrade 中的核心路径。
- [ ] 失败时保留 trace、截图、前端 console 和后端日志。
- [ ] nightly 执行现有 workspace stress，并上传 `frontend-metrics.json`。
- [ ] nightly 或手动门禁增加 Firefox/WebKit 最小兼容性 smoke；PR 默认只跑 Chromium。
- [ ] 为 E2E 设置独立数据库和端口，禁止与其他 worktree/CI job 共享运行时状态。

验收：

- PR E2E 冷启动后总耗时控制在 3 分钟内。
- E2E 不访问真实外部服务，连续十次执行无 flaky。
- nightly 可以查看 click latency、long task、内存、SSE throughput 和 trace artifact。

回滚：PR E2E 可临时降为非阻塞 job，但 nightly stress 和 artifacts 保留用于诊断。

### Phase 5：CI 拆分、依赖去重与 flaky 治理

目的：在测试语义稳定后优化 CI 拓扑，避免提前并行化掩盖结构问题。

任务：

- [ ] 根据 Phase 0 数据决定 backend unit/integration 是否拆成并行 jobs。
- [ ] 将 OpenAPI contract 生成放到 backend/独立 contract job，评估是否可以让 frontend job
      不再安装完整 Python 环境和 PostgreSQL service。
- [ ] 保留 uv/npm/cargo cache，并记录 cache miss 对冷启动的影响。
- [ ] 将全局 `--reruns 1` 改为可观测策略：发生 rerun 时 job summary 报告；nightly 可选择
      fail-on-rerun。
- [ ] 对已知 flaky 用例建立 owner、原因、截止日期，禁止永久静默重试。
- [ ] 建立耗时预算：单元测试文件、集成测试文件、E2E 场景分别监控。

验收：

- develop CI 连续三次中位数不超过 6 分钟。
- 任一 rerun 在 CI 页面可见并能定位到 test id。
- frontend job 不再为非前端工作重复启动重型依赖，或有数据证明保留更快。
- required checks 和 branch protection 在 job 改名/拆分后同步更新。

回滚：保留旧聚合 job 一段迁移期；新并行 job 稳定后再调整 required checks。

## 6. 建议提交与评审边界

每个阶段拆成可独立回滚的小提交：

1. `test(ci): add timing and junit telemetry`
2. `test(py): make postgres fixtures opt-in`
3. `test(py): classify unit and postgres suites`
4. `test(frontend): split node and jsdom projects`
5. `test(frontend): make coverage inventory explicit`
6. `test: cover critical dispatch and workflow upgrade gaps`
7. `test(e2e): add deterministic browser smoke flows`
8. `ci: run browser smoke and nightly stress`
9. `ci: split test lanes from measured evidence`

每个提交都需在描述中附带修改前后耗时、测试数量、coverage 变化以及是否发生 rerun。

## 7. 每阶段统一验证清单

- [ ] `git status --short` 只包含当前阶段预期修改。
- [ ] 执行最小相关测试，并记录命令与结果。
- [ ] 执行 `./scripts/check-quick.sh`。
- [ ] 涉及 coverage、CI、fixture 或测试选择时执行 `./scripts/check.sh`，或等待对应 GitHub
      Actions full gate。
- [ ] 检查 collected/passed/skipped 数量，解释任何变化。
- [ ] 检查 coverage 分母、百分比和低覆盖文件，不能只看总百分比。
- [ ] 检查测试日志中的 warning、ResourceWarning、console error 和 rerun。
- [ ] 涉及并发/数据库隔离时，至少重复执行三次相关测试。

## 8. 风险控制与停止条件

出现以下任一情况时停止扩大改动，先修复当前阶段：

- 测试数量无解释下降。
- 关闭 PostgreSQL 后 unit suite 仍尝试连接数据库。
- xdist 并发下出现跨 worker 数据污染。
- `fresh_schema` 测试留下 DDL 漂移。
- coverage 提升来自排除生产文件或测试 support 进入分子。
- E2E 依赖真实凭据、互联网或共享数据库。
- rerun 掩盖可复现失败。
- CI 提速仅来自降低断言、跳过测试或放宽门槛。

## 9. 完成定义

本计划完成需要同时满足：

1. Python unit 层可无 PostgreSQL运行。
2. integration/full 层数据库隔离证据通过。
3. 前端 logic/component 环境分离，1058 个基线用例无损保留。
4. 关键低覆盖模块达到分区目标，coverage 分母显式且可审计。
5. PR 有确定性 browser smoke，nightly 有压力测试和可下载证据。
6. CI 具备耗时、JUnit、rerun、coverage、trace 可观测性。
7. 连续三次 develop CI 中位数不超过 6 分钟，且全部 required checks 通过。
8. 文档和本地质量门说明与最终实现一致。

## 10. Quality Impact

正向影响：

- 单元测试不再需要基础设施即可运行，开发反馈更快、更可靠。
- 数据库集成测试的边界更明确，隔离失败更容易定位。
- coverage 从聚合数字转为可审计的风险信号。
- 浏览器 E2E 补上 jsdom 无法覆盖的导航、媒体、SSE 和真实构建集成。
- CI 的耗时与 flaky 趋势可见，后续性能回归能够被及时发现。

潜在代价：

- 初次测试分类会产生较大的机械性 marker/fixture 修改。
- CI job 拆分可能增加总计算分钟数，即使关键路径变短；需用 Phase 0 数据权衡。
- E2E 增加浏览器安装与维护成本，因此 PR 只保留少量确定性 smoke，压力和多浏览器放在
  nightly。
- 更严格的分区或 changed-lines coverage 会在短期暴露技术债，建议分阶段从报告模式切换
  到阻塞模式。

总体原则：任何提速都不能以减少有效测试、降低覆盖门槛或隐藏 flaky 为代价。
