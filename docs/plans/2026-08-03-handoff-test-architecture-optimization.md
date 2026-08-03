# 测试架构优化——交接文档（2026-08-03 第二版）

> 第一版见同文件 git 历史。本文档是自包含的交接入口；权威执行记录以
> `docs/plans/2026-08-01-test-architecture-optimization.md` 为准（含 Phase 0–5 全部
> 执行记录、CI run 链接、flaky 观察清单）。

- Worktree：`/Users/lucius/GitHub/agent-legion/.worktrees/test-architecture-optimization`
- 分支：`test/test-architecture-optimization`（已推送 origin，工作区干净）
- 基线：`develop@836235b9`

## 1. 当前进度快照

- **Phase 0–4 全部完成并 CI 验收**。Phase 3：后端 6 个低覆盖模块 + 前端 4 个盲区簇
  全部补到目标线，coverage 分母显式（359/359），分区门槛报告模式运行
  （`scripts/check_coverage_partitions.py`，10 分区全 OK）。Phase 4：`frontend/e2e/`
  3 个确定性 smoke spec + `e2e-smoke` PR job（2.5 分钟）+ `nightly-e2e`（三浏览器
  smoke + stress + `frontend-metrics.json` 上传，14.6 分钟，schedule/dispatch）。
- **Phase 5 进行中**（CI 拓扑，目标 develop CI 连续三次中位数 ≤6 分钟）：
  - 5A/5B ✅（`2aceae5f`）：api:check 移到 backend；frontend 拆
    logic/component/coverage 三 job（blob 合并 coverage）。CI 实测 frontend 关键路径
    9m06s → **3.8 分钟**。
  - 5C ✅ 本地完成（`8d9881d4`）：backend 拆 backend-unit（无 PG，~3 分钟）/
    backend-postgres（合并 coverage，预计 ~8 分钟）。**CI 复验 run
    [30805689114](https://github.com/LuciusCao/agent-legion/actions/runs/30805689114)
    已 dispatch，恢复后第一件事：`gh run watch 30805689114 --exit-status` 确认。**
  - stress probe 阈值 env 化（`697a106f`，`STRESS_PROBE_TIMEOUT_MS` /
    `STRESS_MAX_PROBE_ERRORS`）已修复 nightly 首跑的偶发探针超时。

## 2. 剩余任务（按序）

1. **确认 5C CI run 30805689114 结果**，把验收行补进计划文档 Phase 5C 记录
   （"待确认"处）。若失败先看是否 artifact 时序/合并机制问题（子代理风险项见
   Phase 5C 记录），不要轻易重跑了事。
2. **5C-2（很可能需要）**：backend-postgres 预计 ~8.2 分钟，达不到 ≤6 分钟。把
   postgres tier 再分片（2 个 shard，机制参照 frontend blob：独立 COVERAGE_FILE +
   artifact + coverage combine；会动 `scripts/check-quick-backend.sh` tier 语义，
   必须同步 `tests/test_quality_gate_scripts.py` 契约测试）。如果 5C 实测
   backend-postgres ≤6 分钟可跳过。
3. **5D flaky registry**：把计划文档「Phase 5D 观察清单」落成正式 registry
   （owner/原因/截止日期）；nightly 可选 fail-on-rerun。
4. **Phase 5 验收**：连续 3 次 CI（workflow_dispatch 即可）中位数 ≤6 分钟
   （PR 路径，不含 nightly-e2e）；required checks 在合并 PR 时同步为
   `backend-unit / backend-postgres / frontend-logic / frontend-component /
   frontend-coverage / rust / e2e-smoke`（repo settings 手工操作，合并前做）。
5. 全部完成后：整理本分支 PR 合入 develop。

## 3. 标准流程与门禁（每簇/每阶段）

- 本地门禁：`export AGENT_LEGION_TEST_WORKERS=4 && ./scripts/check.sh`（含 quick
  gate + full gate + combined coverage；**gate 运行期间不要改动工作区文件**）。
- 提交：Conventional Commits，message 带前后耗时/测试数/coverage/rerun 指标。
- 推送：pre-push 要求工作区干净（有杂文件先 `git stash -u`，推完 pop）；禁止
  `--no-verify`。
- CI：本分支不会自动触发，必须 `gh workflow run "Quality Gate" --ref
  test/test-architecture-optimization`，然后 `gh run watch <id> --exit-status
  --interval 90`。dispatch 会同时触发 nightly-e2e（多 ~15 分钟，属正常）。
- 计划文档每个阶段补执行记录（指标 + CI run 链接），格式照抄已有记录。

## 4. 环境坑（都踩过）

- worktree 运行时 PG 库被外部 drop → export_openapi 阶段 PoolTimeout；重建
  `CREATE DATABASE agent_legion_test_architecture_optimization`（或
  `scripts/init-worktree.sh`）。
- 本机多 worktree 并行负载 40+ 时：frontend 组件测试 5s timeout 批量抖动、
  backend `test_local_executor_cancel_during_run` flaky、xdist coverage combine
  丢数据一次——隔离复跑确认后等负载回落重跑 gate。
- 新前端文件先 `npx prettier --write`；新 Python 文件先 `ruff format`；
  新生产文件必须 `uv run python -m scripts.ratchet_architecture_budgets` 登记预算，
  超预算拆分而非抬 ceiling。
- stress 后端（`server.app.main:app`，start_worker=True）启动即跑
  `validate_settings`：CI 无 whisper/CMS 凭据会挂——smoke 用 factory app 跳过
  校验，stress 用 `scripts/stress/_validation_stub.py` 的 stub 绕过（已修好，
  勿回退）。
- `batch_by_ids` intake 真实调 CMS `/question/detail`：真实进程 E2E 必须有
  CMS stub（`scripts/e2e/run_browser_smoke.py` 内置），pytest 里是 monkeypatch。
- CI 偶发：Docker Hub 拉 postgres:17 超时（`gh run rerun <id> --failed`）、
  artifact API 503（重试即可）。

## 5. 纪律红线

- 任何提速不得以删断言/跳测试/降门槛为代价；rerun 不得掩盖可复现失败。
- 质量门未过不声明完成；提交前 `git status --short` 只含当前任务文件。
- 计划文档停止条件见 §8。
