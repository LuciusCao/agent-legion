# 实例设置旧概念治理方案（proposal）

状态：**提案**（需求记录，未实施；评审通过后另立分支落地）
提出背景：全局设置页打磨（settings-ux-polish，2026-09-02）的实例设置区块
review——两个暴露在 admin UI 的概念（「启用工作流」「代码池/code_capacity」）
被产品侧判断为"看起来很老"，需要判定其现存业务影响并给出治理与退役路径。

## 1. 现状核实（本次 review 已确认的事实）

### 1.1 「启用工作流」（`workflows.enabled`）

- **语义**：不是"工作流功能"的业务开关，而是**进程级执行开关**，有两个
  消费面：
  1. `worker_startup.py:47`（`WorkflowWorkerThread.is_enabled`）：关掉则不
     启动 Sweeper 与 WorkflowWorkerThread，本进程不承担执行职责；
  2. `server/app/routes/job_http.py::require_workflows_enabled`：被 jobs、
     workflow revisions、skills、job mutations/artifacts、studio agent
     tools 等 15 个路由模块共 125 处调用——关掉后**这整套 workflow API
     直接返回 404**（"Workflows are disabled"），不只是后台不执行。
- **现存业务影响**："同一份代码，按部署形态决定是否承担执行与 workflow
  API 职责"。单机部署（当前唯一支持的形态）下它**没有任何合理的关闭理由**：
  关掉 = 所有任务永远不执行 + 全部 workflow API 404。
- **为什么还在**：多副本部署预案的雏形（API 副本关执行、专属 worker 副本开
  执行）。但多副本至今未落地，且真正的多副本拆分方案（独立 worker 进程）
  也不该复用这个实例级 DB 设置——那是部署编排（compose/env）的事。
- **结论**：对 admin UI 而言是**过时概念**。

### 1.2 「代码池」（`code_capacity`）

- **语义**：单个隐含 code 执行池的线程数上限（`workflow_worker/pools.py`：
  `ThreadPoolExecutor(max_workers=code_capacity)`，重启生效）。
- **现存业务影响**：真实且活跃——所有非 Agent 路由节点（schema v47 后的
  `type: code` 节点）都跑在这个池里，容量满了会 `code_capacity_full` 跳过
  （`agent_broker/claim_evaluate.py`，2026-08-18 还修过它的饿死 bug）。
- **结论**：**不是过时概念**，是真实的容量调优参数；但"代码池"这个名称是
  执行器时代（executor registry/kinds 机器，v47 已退役）的残留，对不了解
  内部实现的用户没有意义。

## 2. 治理方案

### 2.1 短期（随本 PR，settings-ux-polish，已实施）

- 实例设置区块默认折叠进「高级参数」，每组补一句面向用户的说明——旧概念
  不再默认暴露给管理员。本提案记录的是其后的独立退役工作。

### 2.2 退役「启用工作流」（建议方向：下线 UI + 保留 env 兜底）

1. **UI 下线**：从实例设置表单与契约中移除 `workflows.enabled`
   （`instanceSettingsFields.ts` / `InstanceSettingsSection` /
   `instance_settings.py` 文档默认值）。
2. **运行时开关改 env**：`AGENT_LEGION_DISABLE_EXECUTION=1`（或同类
   显式 env）替代 DB 设置，进程启动时读取一次，**两个消费面都改读同一
   env**——`worker_startup.is_enabled` 与 `require_workflows_enabled`
   （后者影响 15 个路由模块 / 125 处调用的 404 行为，需随开关语义一起
   迁移并逐路由确认）。DB 键保留读时剥离（同 `openclaw` 块先例，
   `_strip_retired_blocks`），存量文档不需要数据迁移。
3. **多副本部署落地时**重新评估：届时若需要 per-副本 开关，应由部署编排
   （compose profile / env）承担，而不是回到实例级 DB 设置。

### 2.3 「代码池」改名不退役

1. UI label 从「代码池 / code 池容量」改为「并发执行上限」类人话表述，
   保留 `code_capacity` 契约字段名（避免无收益的契约 churn）。
2. 说明文案已随本 PR 落地（「代码节点共享的执行线程池大小，即同时执行的
   代码节点数」）；改名项与后续可能的 admin UI 全局文案统一一并做。

## 3. 风险与依赖

- `workflows.enabled` 下线涉及实例设置契约变更（前端 generated types、
  `instance_settings.py` 默认文档、hydration 路径），需走完整的
  contract → service → route 扩展顺序。
- 存量部署的已存 DB 文档会继续携带该键，读时剥离先例（`openclaw`，
  `instance_settings.py:71`）已验证该模式可行。
- 与 #359（runtime profile）可能存在概念交叠（执行侧运行时配置归置），
  落地前先对齐方向，避免两次迁移。

## 4. 验收口径

- admin UI 与 OpenAPI 契约不再出现 `workflows.enabled`。
- 存量 DB 文档含该键时 GET 返回值不含它，PUT 不再接受它（extra=forbid
  已有防线会拒绝未知键，需确认剥离发生在验证之前）。
- 单机默认部署行为不变（执行线程照常启动）。
