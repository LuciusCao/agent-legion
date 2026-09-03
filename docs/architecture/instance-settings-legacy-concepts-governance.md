# 实例设置旧概念治理方案（已实施定稿）

状态：**已实施**（#385 / #386 / #389，feat/host-control-plane-389）
原提案（「UI 下线 + env 兜底」方向）经历史调研推翻，本文按实际落地定性重写；
实施前的原始提案表述见 git 历史（本文件在本 PR 之前的状态）。

## 1. 定性（历史调研结论）

### 1.1 「启用工作流」（`workflows.enabled`）——退役

- **语义漂移史**：2026-06-20 `ea98c240`（config 域拆分）引入时是**新旧功能并存的
  灰度开关**（Video Hive 视频队列 vs workspace/job DAG 工作流）；旧功能面陆续退场
  （Video Hive、openclaw runtime、`workflows.pi`）后 DAG 工作流成为产品本体，
  开关语义漂移为**事实上的产品总开关**；2026-08-07 `16921414`（实例级配置产品化）
  原样迁入 `global_settings` 时灰度语境已消失，且无文档记录该漂移。
- **两个消费面**：`worker_startup.py` 的 `is_enabled`（关掉则整条执行栈不启动）与
  `require_workflows_enabled` 404 门禁（38 个路由文件、约 150 处调用，覆盖产品
  核心 API 面）。
- **单机部署（唯一支持形态）下无任何合理关闭场景**：关掉 = 所有任务永远不执行 +
  全部 workflow API 404。
- **处置**（#389 第 3 步收编，比原提案的「env 兜底」更彻底）：
  - 404 门禁**整体移除**（API 面永远可用）；
  - `worker_startup` 的总开关职责由 `code_capacity == 0`（纯控制面模式）+
    `sweeper_enabled=False` 逃生舱承担；
  - DB 布尔退役：契约删除该键、存量文档读时**键级剥离**（`workflows` 块的
    `max_items_per_run` 活跃保留，`instance_settings.py::_strip_retired_blocks`），
    无数据迁移；旧前端在升级窗口内整文档 PUT 携带该键会 422（可接受的破坏性
    契约变更）。

### 1.2 「代码池」（`code_capacity`）——保留改述 + 0 合法化

- **实际语义**（#386 定性）：**本地兜底执行器容量**。code 节点执行是双路径的：
  远程 code Worker 在线且 payload 合格时优先远程（容量由各 Worker 自报的
  `max_code_concurrency` 决定，经 worker 控制台热更）；无 Worker 可用时回落宿主
  本地沙箱（velites 子进程 + ThreadPoolExecutor），`code_capacity` 同时是本地池
  大小与本地 lease 上限。与 Worker 侧同名容量声明**只是恰好同名，无关联**。
- **三层容量体系**（各管各的，无跨层开关）：实例 `code_capacity`（本地兜底）→
  Worker `max_code_concurrency`（远程执行，agent-only 默认 0）→ workspace
  `node_limits` / `agent_capacity`（节点/workspace 级）。
- **处置**（#386 + #389 第 1 步合并实施）：
  - 契约 `gt=0 → ge=0`：**0 = 纯控制面模式（纯远程）**——宿主不组装本地执行栈
    （CodeExecutor / ExecutionRuntime / 线程池都不建，velites 二进制依赖从该
    部署形态中消失），需要在线 code Worker 才能推进；
  - UI label「代码池 / code 池容量」→「**本地执行并发上限（0 = 纯远程模式）**」，
    分组「代码池」→「本地执行」，说明文案改为兜底语义（扩容走 Worker 而非调大
    宿主本地池）；默认值维持 16（纯远程是显式运维选择）；
  - shard 节点的分片执行同样先远程后本地（此前只有本地路径）。

## 2. 产品责任点

纯控制面 + 无在线 Worker = workflow **静默停摆**（任务排队、无错误）。配套健康
信号：`/api/health` 的 `workers` map 在纯远程模式下实时报告
`execution_mode=pure_remote` 与 `online_code_workers`（启动时为 0 打 WARNING 日志）。

## 3. 验收口径（已达成）

- admin UI 与 OpenAPI 契约不再出现 `workflows.enabled`；存量 DB 文档含该键时 GET
  剥离（响应验证之前），PUT 不再接受（extra=forbid）。
- `code_capacity` 接受 0；纯远程模式下无本地执行栈组装、调度线程照常运行（早退
  修复：pass 级早退加入「无在线 code Worker」维度）、code 节点不回落本地执行。
- 单机默认部署行为不变（默认 16，执行栈照常组装）。
