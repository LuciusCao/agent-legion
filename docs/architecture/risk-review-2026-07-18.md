# 架构 Review（基准：develop，2026-07-18）

> **时点快照声明（2026-08-04 补注）**：本文是 2026-07-18 时点的历史记录，结论与
> `path:line` 证据均反映当时代码，未随后续演进更新。多条结论此后已失效，包括：
> SQLite 已迁移至 PostgreSQL；远程执行已从 `executors/remote_broker.py` 的内存队列
> 重构为 `server/app/agent_broker/` 体系；进程内事件机制已收敛到 EventBus
> （`server/app/events/bus.py`）；调度轮询已补 wakeup 机制
> （`server/app/scheduler_wakeup.py`）；文中引用的 `executors/remote_broker.py` 等
> 文件已删除。当前状态请以现行代码与 `docs/architecture/` 现行文档为准。

目标：评估扩展性与可维护性，并明确向"分布式 Agent 协作系统"演进的路线。

审查方式：4 路并行只读审查（后端分层与模块边界 / 执行器与 capability 体系 / 持久化并发与状态一致性 / 前端与全栈契约），所有结论均有 `path:line` 级证据。

## 总体判断

代码健康度在同类项目里属于上游：边界纪律有 17 个架构门禁测试强制执行，capability 与 executor 真正分层，租约并发语义扎实，无 god module。主要债务集中在三处：事件机制的历史堆叠、legacy video 体系半退场状态、以及——对分布式演进最关键的——所有运行时状态都在单进程内存里。

## 一、做得好的（应保留的资产）

- 架构治理体系：`config/architecture/` registry + 门禁测试 + evidence matrix，invariant 变更有据可查。
- capability 解耦成立：workflow YAML 只声明 `capability`，binding 按 `(workspace, workflow, node)` 运行时查表；新增 capability 到现有 executor 只需改 YAML。
- 租约协议语义正确：`begin immediate` 事务 claim、TTL + heartbeat、孤儿 job 恢复、远程结果 staging + 原子 rename。
- SQLite 基线配置正确：WAL、busy_timeout、有界重试、19 个版本化迁移。
- 前端契约纪律基本落实：`generated/api.ts` 有漂移门禁（`api:check`），store 分层清晰，事件 hook 有完整重连/排队。

## 二、关键问题（按严重度）

### P0 — 分布式演进的结构性障碍

1. **Worker 与 Web 进程同生共死**：`WorkflowWorkerThread` 在 FastAPI lifespan 里以 daemon 线程启动（`server/app/main.py:141-151`），执行容量无法独立扩缩容。
2. **核心状态全部进程内**：pause 状态、round-robin、`JobEventBuffer` revision、agent busy/idle、`_pools/_futures`——多副本部署立即状态分叉；两个节点会重复执行本地 handler（lease 只保护 DB claim，不保护内存 future 跟踪）。
3. **远程执行队列是纯内存 dict**（`server/app/executors/remote_broker.py:83-84`）：server 重启丢全部 queued/claimed 任务，永远无法多节点共享；注释自称 "sqlite-backed" 但只持久化了 worker registry，文档与实现错位。
4. **事件总线 100% 进程内**：5 套事件机制并存（`events.py` 两个 manager、`JobEventBuffer`、`agent_broadcast.py` 轮询、`NotificationHub` 回调），全部走内存 queue，跨节点事件不可达；revision 语义只在单进程成立。
5. **调度单点 + 轮询**：每个 tick 对每个 workflow definition 全表扫描未完成 job（`server/app/workflow_worker_thread.py:148-153`），O(definitions × jobs)，无 notify/wakeup 机制。

### P1 — 分布式就绪度缺口

6. **`kind: remote` 名不副实**：`RemoteExecutor` 实为 "remote-pi"（`server/app/executors/remote.py:42-86` 直接构造 pi manifest），OpenClaw 无法远程执行；server/worker 双端有三处手工镜像代码（prompt/command/error-scan，`scripts/remote/worker.py:37,105-141`）。
7. **容量模型两端不一致**：worker 注册存了 `slots` 但 `dequeue` 从不检查（`remote_broker.py:116-133`），`--slots 65` 实际无效；每个远程执行阻塞一个线程池线程（`remote.py:109` 同步 `wait_result`），`global_capacity: 100` = 100 个阻塞线程。
8. **新增 executor kind 要改 4 处硬编码**（`executors/config.py:115-118` 判别联合、`registry.py:94-130` isinstance 链、`runtime_config.py`、`main.py` 装配），不是可注册工厂。
9. **无多 Agent 协作原语**：无 agent→agent 消息通道、无 fan-out/shard/reduce 节点类型、无跨节点 artifact 寻址（bundle 整体搬家）、worker 注册无 per-worker 凭证/标签/路由亲和（全 worker 共享一个静态 token，`routes/remote.py:46-56`）。
10. **SQLite 单文件是全系统写串行点**：心跳、claim、UI 读全打 `data/video_hive.sqlite`；Repository 抽象名不副实，SQL 散落在 `db/queries/`、`jobs/queries/`、`executors/` 三处，迁外部 DB = 重写。

### P2 — 可维护性债务

11. **legacy 双轨残留**：`WorkerThread`/`worker.py`/`worker_candidates.py` 运行时已无人启动（仅 tests 引用）；`workflows/executor.py` 的 `execute_node_once` 绕过 lease 直调 `PiRunner`；`AgentStatusManager` 直接 import legacy pipeline 的 openclaw 发现（`agents.py:9`）；`legacy_migration.py`（411 行）长期驻留主包。
12. **main.py 穿透封装**：直接写 `agent_manager._broadcast_controller`、`video_event_manager._loop` 等私有属性（`main.py:119,125-126`）；`routes/__init__.py:40-54` 又在自己的工厂里 new service，双组装点。
13. **前端手写 transport 类型残留**（唯一明确违反 AGENTS.md 红线处）：`frontend/src/types.ts` 的 `VideoItem`、`PhaseRun` 等死类型 + `VideoContentPanel` 的逆向适配层 `buildVideoItem`（`VideoContentPanel.tsx:57`）。
14. **前端 api 层迁移半途而废**：`/api/jobs/{id}` 资源族分散在 `api.ts`/`jobApi.ts`/`jobBatchApi.ts` 三个文件，33 个文件仍从旧入口导入。
15. **三条实时通道各自为政**：dashboard SSE 无重连，agents WS 固定 3s 重试无退避（`uiStore.ts:74-80`），无连接健康度状态暴露。

## 三、演进路线（朝分布式 Agent 协作系统）

按依赖顺序，每步独立可交付：

### 阶段 1 — 清障（低成本，立刻可做）
- 退役 legacy video worker 全组文件及测试，清理前端死类型（P2 全部）。
- 统一事件层：收口为单一 `EventBus` 接口（进程内实现为默认），消灭 `__dashboard__` 魔术 id 和 main.py 私有属性穿透。

### 阶段 2 — 状态外置（分布式前置条件）
- `RemoteExecutionBroker` 队列落 SQLite（复用 lease 事务范式，`update ... where state='queued'` 原子抢占），补齐 slots 语义。
- 事件 revision 改由 DB 序列发放；worker pause/round-robin 状态落库。
- 抽 `Transaction`/Unit-of-Work 上下文，收敛 6+ 份重复的连接管理代码。

### 阶段 3 — 执行模型解耦
- 消除 `wait_result` 阻塞模型：broker 完成回调直接走 `leases.finish`，远程容量与线程数解耦。
- `kind: remote` 泛化为传输层，pi/openclaw 作为 payload 类型；消除 server/worker 三处镜像代码。
- executor kind 改为可注册工厂表；增加独立 sweeper 角色（lease 过期不依赖 worker 线程存活）。

### 阶段 4 — 多 Agent 协作（当前形态的真正增量）
- workflow schema 增加 shard/fan-out + reduce 节点类型。
- content-addressed artifact store，替代 bundle 整体搬家。
- worker 注册引入 per-worker token + 标签（机型/模型端点），dequeue 支持亲和与优先级。
- 前端建 executor/claim 状态模型（generated schema 里已有 `ClaimResponse` 等，无人消费），DAG 节点显示执行者身份。
