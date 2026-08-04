# 测试架构优化——交接文档（2026-08-03 第三版，终态）

> 2026-08-04 补充：develop 27 个提交已合入（merge `ef61d81d`，9 个冲突文件解决，
> 语义见合并提交 message）；codex review 两条 P2 已修（`0bcaf9c1`：ci-extended 补
> `--reruns 1`、轮询名单加 `backend-unit-coverage`）；契约测试 harness 环境泄漏修复
> （`931edc94`）。合并后 CI 复验全绿（run
> [30872022287](https://github.com/LuciusCao/agent-legion/actions/runs/30872022287)，
> PR 关键路径 5.7 分钟）。PR #25 处于可合并状态。

> 第一、二版见同文件 git 历史。本文档是自包含的交接入口；权威执行记录以
> `docs/plans/2026-08-01-test-architecture-optimization.md` 为准（含 Phase 0–5 全部
> 执行记录、CI run 链接、flaky registry）。

- Worktree：`/Users/lucius/GitHub/agent-legion/.worktrees/test-architecture-optimization`
- 分支：`test/test-architecture-optimization`（最新提交见 git log；工作区状态
  以 `git status` 为准——终版文档提交时如遇到本地门禁约束，push 可能留待确认）
- 基线：`develop@836235b9`

## 1. 最终状态：全部 Phase 完成，验收通过

- **Phase 0–4 全部完成并 CI 验收**（Phase 3 覆盖率补点、Phase 4 E2E smoke +
  nightly stress，详见计划文档执行记录）。
- **Phase 5 完成（CI 拓扑 + flaky 治理）**，三次连续 CI 验收通过：

| run | 结论 | PR 关键路径 | backend a/b/c | frontend-component |
| --- | --- | --- | --- | --- |
| [30820753433](https://github.com/LuciusCao/agent-legion/actions/runs/30820753433) | 全绿 | 5.0 min | 4.6/4.6/3.8 | 5.0 |
| [30822164844](https://github.com/LuciusCao/agent-legion/actions/runs/30822164844) | 全绿 | 5.7 min | 5.4/5.0/3.8 | 5.7 |
| [30823425046](https://github.com/LuciusCao/agent-legion/actions/runs/30823425046) | 全绿 | 5.8 min | 5.0/4.6/3.6 | 5.8 |

  PR 关键路径中位数 **5.7 分钟 ≤ 6 分钟目标**（基线 9.2 分钟，降 38%）。
- 最终 CI 拓扑（PR/push 必跑）：`backend-unit`（无 PG，~2.6min）/
  `backend-postgres-a`（api:check + shard 1/3 + coverage 合并 + 85% floor +
  summary）/ `backend-postgres-b`（shard 2/3 + full gate）/
  `backend-postgres-c`（shard 3/3）/ `frontend-logic` / `frontend-component` /
  `frontend-coverage` / `rust` / `e2e-smoke`；nightly/dispatch 限定：
  `ci-extended`（含 fail-on-rerun）与 `nightly-e2e`（三浏览器 smoke + stress）。
- 分片机制：`GATE_SHARD=i/n` + `scripts/pytest_gate_shard.py`
  （`md5(nodeid) % n` collection 过滤）；本地不设 env 时行为与拆分前逐字节一致。
- flaky 治理：`tests/flaky_registry.yaml`（FLAKY-001~005，owner/原因/deadline
  或 recurring）+ `scripts/check_reruns.py`（nightly ci-extended 对 registry 外
  rerun 判红）。

## 2. 剩余事项（仅此两件）

1. **合并 PR 到 develop**（合并前问用户）。
2. **合并时同步 required checks**（repo settings 手工操作）：设为
   `backend-unit / backend-postgres-a / backend-postgres-b / backend-postgres-c /
   frontend-logic / frontend-component / frontend-coverage / rust / e2e-smoke`。
   旧 job 名（`backend`、`frontend`）与新拓扑完全不同名，必须更新 branch
   protection，否则 PR 永远等不到检查。

遗留跟进（不阻塞合并）：flaky registry 三条非 recurring 条目 deadline
2026-09-01（FLAKY-001 cancel-during-run 时序根治、FLAKY-002 jsdom 重负载渲染、
FLAKY-003 xdist coverage combine 丢数据）；Phase 5 任务里"耗时预算监控"未做，
已标注转入后续。

## 3. 过程中修掉的结构性问题（供评审参考）

- **full gate 分片自执 floor**（`d44fe476`）：5C 拆分后 tests/full 步落在独立
  COVERAGE_FILE 上漏 `--cov-fail-under=0`，在 58.71% 部分数据上误判红。
  契约测试已钉住"分片不得自执 floor"。
- **聚合竞态**（`88d815e8`）：2 分片时聚合方 A 自带工作量小于上传方 B，
  必然先撞合并点（run 30811145691 `Artifact not found`）。改 3 分片 +
  A 下载前 `gh api` 轮询等 B/C artifact（10 分钟超时即红）。
- **FLAKY-002 高频单点**（`f874381b`）：`InteractionOverlay.test.tsx` 12 选项
  用例 5 次 CI 挂 2 次（5s timeout），加 20s per-test timeout 缓解，根治仍在
  registry 跟踪。
- **Playwright 冷下载卡 41 分钟**（`60897556`）：吃掉 nightly 45 分钟预算致
  cancel。e2e job 加 `actions/cache` 缓存 `~/.cache/ms-playwright`。

## 4. 标准流程与门禁

- 本地门禁：`export AGENT_LEGION_TEST_WORKERS=4 && ./scripts/check.sh`；
  **gate 运行期间不要改动工作区文件**。
- 提交：Conventional Commits，message 带指标；推送前工作区干净；禁止
  `--no-verify`。
- CI：本分支不自动触发，`gh workflow run "Quality Gate" --ref
  test/test-architecture-optimization` + `gh run watch <id> --exit-status`。
  dispatch 会连带 nightly-e2e（~15 分钟）。
- 新前端文件先 `npx prettier --write`；新 Python 文件先 `ruff format` +
  `uv run python -m scripts.ratchet_architecture_budgets` 登记预算。

## 5. 环境坑（都踩过）

- worktree 运行时 PG 库被外部 drop → 重建
  `CREATE DATABASE agent_legion_test_architecture_optimization`。
- 本机多 worktree 并行负载 40+ 时：frontend 组件测试 5s timeout 批量抖动、
  backend `test_local_executor_cancel_during_run` flaky、xdist coverage
  combine 丢数据一次——隔离复跑确认后等负载回落重跑 gate。
- stress 后端启动即跑 `validate_settings`：CI 无 whisper/CMS 凭据会挂——
  stress 用 `scripts/stress/_validation_stub.py`（勿回退）；E2E 必须有 CMS
  stub（`scripts/e2e/run_browser_smoke.py` 内置）。
- CI 偶发：Docker Hub 拉 postgres:17 超时、artifact API 503、Playwright 冷
  下载慢（已加 cache）——`gh run rerun <id> --failed` 即可。
- 本地合盖/断网只会让 `gh run watch` 进程失败，CI run 本身不受影响；
  重新 `gh run view <id>` 查状态再继续。

## 6. 纪律红线

- 任何提速不得以删断言/跳测试/降门槛为代价；rerun 不得掩盖可复现失败
  （registry + nightly fail-on-rerun 已制度化）。
- 质量门未过不声明完成；提交前 `git status --short` 只含当前任务文件。
