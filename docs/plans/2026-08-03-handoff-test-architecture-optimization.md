# 测试架构优化——交接文档（2026-08-03）

> 写给下一位接手者（可能是新账号的全新会话）。本文档是自包含的交接入口；
> 权威执行记录以同目录计划文档为准。

- Worktree：`/Users/lucius/GitHub/agent-legion/.worktrees/test-architecture-optimization`
- 分支：`test/test-architecture-optimization`（已推送 origin，工作区干净）
- 基线：`develop@836235b9`
- 计划文档（含每阶段验收数据与全部执行记录）：
  `docs/plans/2026-08-01-test-architecture-optimization.md`

## 1. 当前进度

计划共 6 个 Phase（0–5）。Phase 0/1/2 已完成（Phase 2 的 CI 150 秒性能目标
open，刻意延后到 Phase 5 统一做分片）。Phase 3（coverage 分母 + 补盲区）进行中：

| 簇 | 提交 | 结果 | CI 验收 |
| --- | --- | --- | --- |
| 3A coverage 显式分母 + inventory | `7b11fe16` | 359/359 生产文件入分母 | ✅ |
| 3B 前端 auth/bootstrap + app startup | `d3373a34` | 5 个 0% 文件 → lines 100% | ✅ |
| 3C 后端 agent dispatch/pool | `1cc69481` + `d52acb26` | 54%/58% → 100%，顺带修 2 个真 bug | ✅ |
| 3D 后端 workflow upgrade | `506a3cca` | route 0%→100%、service 95%→100% | ✅ run 30777220296 |
| 3E transcription providers | `ff2adb62` | 28%→100% | ✅ run 30779090551 |
| 3F skill version fallbacks | `85dec7c6` | 39%→100% | ✅ run 30780617385 |
| 3G job log raw | `632129cb` | 67%→100% | ✅ run 30780617385 |
| 3H agent artifacts | `36857eb0` | 38%→100% | ✅ run 30780617385 |
| 3I 前端 workflow upgrade（api + hook） | `50331308` | 0%/11% → 均 100% | ⏳ 未 dispatch |

最新本地 full gate（3I）：backend 2378 passed、frontend 144 files / 1095 tests、
full_gate 32 passed、backend combined coverage 93.49%（基线 92.86%）、frontend
lines 88.9%、coverage inventory 359/359、0 rerun。

## 2. 恢复后的第一步

3I 尚未做 CI 验收。先 dispatch 并 watch：

```bash
cd /Users/lucius/GitHub/agent-legion/.worktrees/test-architecture-optimization
gh workflow run "Quality Gate" --ref test/test-architecture-optimization
gh run list --branch test/test-architecture-optimization --limit 1   # 拿 run id
gh run watch <run-id> --exit-status --interval 60
```

注意：Quality Gate 只对 develop/main/master 的 push 自动触发，本分支必须
`workflow_dispatch`（上文命令）。CI 通过后在计划文档对应阶段记录里补一行验收
（格式照抄 3D–3H），docs 提交推送。

## 3. 后续任务队列

按计划文档 §5 Phase 3 剩余任务，仍严格"一个提交一个业务簇"：

1. **3J 前端 API transport 簇**：`frontend/src/api/` 下 10 个 0% 文件
   （authApi、failureApi、jobClearPackedApi、jobFacets、jobSnapshot、metrics、
   tokenUsage、workflow_draft_compare、workflows、jobWorkflowUpgradeApi 已 Done、
   jobBatchApi 10%）。测契约与错误处理；约定照 `src/api/workerTokens.test.ts`
   （mock `global.fetch`，纯 node project，不进 browserTestFiles）。
2. **3K 前端页面簇**：`UsersAdminPage.tsx` 约 61%、`JobDetailPage.tsx` 约 65%
   （以最新 `frontend/coverage/coverage-final.json` 实测为准）。
3. **Phase 3 收尾**：分区/changed-lines 门槛（计划 §5 Phase 3 任务第 4 条，
   建议先非阻塞报告模式）。
4. **Phase 4**：E2E——新建 `frontend/e2e/`，确定性 fixture/seed，PR Chromium
   smoke（≤3 分钟），nightly 压力 + 多浏览器。详见计划 §5 Phase 4。
5. **Phase 5**：CI 拓扑——backend unit/integration 是否拆 job、frontend job 去
   Python/PG 依赖、Vitest 分片（Phase 2 遗留的 CI 150 秒目标在此解决）、flaky
   治理（观测清单见计划文档 3E 记录与 §5 Phase 5）。总目标：develop CI 连续
   三次中位数 ≤ 6 分钟（当前约 9 分钟，瓶颈在 frontend tests）。

前端盲区最新数据用这段脚本实测（coverage-final.json 在 full gate 后更新）：

```bash
cd frontend && node -e "
const cov = require('./coverage/coverage-final.json');
const rows = [];
for (const [file, data] of Object.entries(cov)) {
  const s = data.s, total = Object.keys(s).length;
  if (!total) continue;
  const covered = Object.values(s).filter(v => v > 0).length;
  rows.push([Math.round(covered/total*100), file.split('/src/')[1]]);
}
rows.sort((a,b)=>a[0]-b[0]);
for (const r of rows.slice(0,30)) console.log(r[0]+'%', r[1]);
"
```

## 4. 每个簇的标准流程

1. 实测当前缺口（pytest `--cov=<module> --cov-report=term-missing` 或上面的
   node 脚本），不要信计划文档里的基线数字。
2. 写测试。后端放对应子系统目录（`tests/services/`、`tests/workflows/`、
   `tests/routes/jobs/` 等），不要新增 `tests/` 根目录文件；直接连 PG 的测试
   文件必须登记进 `tests/conftest.py` 的 `_POSTGRES_TEST_FILES`（有审计测试
   `tests/test_pytest_postgres_boundaries.py`）。前端纯逻辑进 node project；
   用 Testing Library/renderHook 的 `.test.ts` 要登记进 `frontend/vite.config.ts`
   的 `browserTestFiles`（jsdom project）。
3. 聚焦测试连跑 3 次（并发/DB 相关时必须）。
4. `./scripts/check.sh`（含 quick gate + full gate 层 + combined coverage；
   `export AGENT_LEGION_TEST_WORKERS=4`）。**gate 运行期间不要改动工作区任何
   文件**——会触发 inventory 审计竞态（3G 踩过）。
5. 计划文档补该簇执行记录（指标：耗时、测试数变化、coverage 前后、rerun 次数）。
6. 提交：Conventional Commits（如 `test(py): cover raw log fallback branches`），
   message 必须带修改前后耗时/测试数/coverage/rerun（计划 §6 要求）。
7. 推送：pre-push 门禁要求工作区干净；若有未提交文件，`git stash -u` 后推，
   推完 `git stash pop`。禁止 `--no-verify`。
8. dispatch CI 并 watch（见 §2 命令），通过后补验收记录。

## 5. 环境坑（都踩过）

- **worktree 运行时 PG 库可能被外部 drop**：`export_openapi` 阶段 PoolTimeout
  （10s）就是这个症状。重建：
  `psql postgresql://127.0.0.1:5432/postgres -c "CREATE DATABASE agent_legion_test_architecture_optimization"`
  （或 `scripts/init-worktree.sh`，幂等）。测试库由 `tests/postgres_support.py`
  按 worktree 名自动派生，不受影响。
- **多 worktree 并行时本机负载会冲到 70+**：frontend 组件测试 5s timeout、
  backend 已知 flaky `test_local_executor_cancel_during_run` 会批量超时。这是
  负载抖动不是回归——隔离复跑确认后，等负载回落重跑 gate 即可。
- **新前端文件先过 prettier**：`npx prettier --write <files>`，否则 static
  round 直接挂（3I 踩过）。
- **JS 默认参数陷阱**：`setup(undefined)` 会触发默认值；hook 测试封装
  setup 函数时把 jobId 设为必传（3I 踩过）。
- tracked `config/agent_legion.yaml` 的 `asr.vad_model` 是机器相关路径，测试
  必须显式覆写 `settings.config["asr"]`（3E 处理过）。
- CI frontend job 偶发 Docker Hub 拉取 `postgres:17` 超时（基础设施），
  `gh run rerun <id> --failed` 重跑即可（3D 踩过）。

## 6. 纪律红线（摘自 AGENTS.md 与计划 §8）

- 任何提速/提覆盖率不得以删断言、排除生产文件、`pragma: no cover`、放宽门槛
  为代价；rerun 不得掩盖可复现失败。
- 不用 `git commit/push --no-verify`；质量门未过不声明完成。
- 提交前确认 `git status --short` 只含本簇文件。
- 停止条件见计划 §8（测试数无解释下降、unit 层连库、xdist 数据污染等）。
