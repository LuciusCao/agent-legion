# Runs 异步模式（#1117）

日期：2026-10-20
状态：现行（Phase 2 核心切片；Studio UI 接线属后续切片）

`POST /workspaces/{id}/runs` 的异步受理模式：大批量 items 提交不再在请求线程内
完成「解析 → 去重 → 建 job」全序列（万级条目会撞上写超时，#1157 phase0 只能兜底
报错），而是秒级受理返回 202 与句柄，由进程内 feeder 按水位滴灌式建 job。本设计
取代 #505/#532 的 Campaign 产品实体方案（PR #541 未进 main，其表形状作为
`run_batches` 的迁移模板）。runs-async 是基础设施，不是产品实体：无列表/注册 API、
无独立生命周期管理面，生命周期跟随 run。

## 1. 请求契约

`RunCreateRequest` 新增 `mode` 字段（`"sync"` 默认，行为与现状逐字节一致；
`"async"` 走异步受理）。异步受理仍做全部**廉价**fail-fast 校验——workspace 与
active revision 存在性、条目形状与 `accepted_item_types` 入口契约、text 条目形状、
text 物化前置条件（对象存储未配置 → 503，与同步路径同）、节点配置解析与 code 版本
冻结——所以被拒的提交照样不留 run 行。逐项解析（材料存在性/就绪、连接可用性）与
去重推迟到 feeder，错误按 chunk 记录（见 §3），不再以 400 拒整批。

响应：

- 同步：200 `RunCreateResponse`（不变）。
- 异步：202 `RunAcceptedResponse` = `{run, batch}`。`run` 是既有 RunRecord（确定性
  run id 与同步路径同 digest 公式，见 §4）；`batch` 是 `run_batches` 句柄
  （状态机 + 游标 + 计数器）。
- 进度轮询复用 `GET /workspaces/{id}/runs/{run_id}`：`RunDetailResponse` 新增可空
  `batch` 字段（仅异步 run 有），`job_stats` 照常返回实时 job 状态分布。
- 控制面：`POST .../runs/{run_id}/pause|resume|cancel`，只作用于 feeder（停止/恢复/
  终止继续建 job）；已建 job 不受 cancel 影响，沿用既有批量操作面管理。三端点与
  POST /runs 同守卫（`require_workspace_api_intake`），路由名登记进
  `API_SCOPE_INTAKE_ROUTE_NAMES`，机器闭环（提交 → 轮询 → 取消）完整。

## 2. `run_batches` 状态行（schema v99）

表形状迁移自 PR #541 的 `campaigns` 裁减版（去掉 mode/watermark/批次计数等
产品字段；`runs` 表不加反向列——最冷路径才 join）：

```
run_batches(
  run_id text primary key references runs(id) on delete cascade,
  workspace_id text not null references workspaces(id) on delete cascade,
  status text not null default 'pending'
    check(status in ('pending','running','paused','failed','completed','cancelled')),
  target_spec_json text not null default '{}',   -- items + 冻结面（见下）
  next_index integer not null default 0,         -- items 游标
  items_total integer not null default 0,
  jobs_created integer not null default 0,       -- 每 tick 重算 count_jobs_in_run
  chunk_errors_json text not null default '[]',  -- 至多 20 条（intake 先例）
  error_message text not null default '',
  created_by text not null default '',
  created_at / updated_at timestamptz, finished_at timestamptz
)
-- idx_run_batches_active: partial (status) where status in ('pending','running')
-- idx_run_batches_workspace: (workspace_id, created_at desc)
```

状态机：`pending`（受理/重置/tick 间隙，等待认领）→ `running`（单次 tick 的
认领态；非末次推进一律回到 `pending` 让下一 tick 立即再认领——intake 消费
者的 chunk 间 queued 先例）⇄ `paused`（操作员意图；feeder 跳过）；终态：
`completed`（游标耗尽）| `failed`（确定性失败：revision 消失、target_spec
损坏等；瞬时 DB 抖动走退避不转 failed）| `cancelled`（任意非终态）。DDL 走
apply fn（postgres_schema.sql 体积预算上限，v76 先例），fresh 与升级库同
路径，parity 测试钉两边一致。

`target_spec_json` 冻结面：`items`（原始提交条目，text 条目按原文携带）、
`workflow_revision_id`、`node_config`、`node_code_versions`、
`text_default_filename`——受理时解析一次，feeder 不再回读 live 配置（与同步路径的
冻结语义一致，RUN-FREEZE-001 / EXEC-CODE-002）。

## 3. Items feeder

复用 intake queue 模式逐件对应：`claim_run_batch`（`for update skip locked`，认领
`pending` 或停滞超 600s 的 `running`）↔ `claim_intake_run`；
`consume_run_batches`（`asyncio.to_thread` 隔离 + 失败退避 1s + 空转 0.5s /
有活 0.5s→0.05s）↔ `consume_intake_batches`；统一由 `startup_tasks.BackgroundTasks`
托管启停。明确**不引入进程池**：消费循环单进程协程 + 线程隔离即够用（瓶颈在 DB
往返，不在 CPU）。

每个 tick 处理一个 chunk（`RUN_BATCH_CHUNK_SIZE=500`；intake 的 25 是按外部 CMS
调用延迟定的，items 解析是纯本地探测）：

1. **水位闸**：workspace 非终态 job 数（counter 表读取，`count_jobs_by_status`
   的 pending+running）≥ 水位时把 batch 放回 `pending` 并空转——`claim` 按
   `updated_at` 排序，多 workspace 间天然轮转公平。水位 = `min(30000,
   workflows.max_items_per_run)`（`max_items_per_run=0` 即不设限时取 30000）。
2. text 条目按 chunk 物化（`materialize_text_items`，content-hash 幂等），其余
   条目直接进解析。
3. 候选解析走 `resolve_run_items` 族（material/bundle/ref 三族 chunked 探测；
   与 intake RESOLVERS 是不同族，不共用）。
4. 去重 + 插入 + 事件记录全部走 M42 的 `JobCreationPipeline`
   （`collect_fresh_candidates` 点探测 → `insert_jobs` → `record_jobs_created`），
   写面因此全部落在已登记的 `job_bulk_sql.py` 门面内。
5. chunk 级失败（坏条目、连接失联、物化失败……）记录进 `chunk_errors_json`
   （最多 20 条）并跳过该 chunk 继续，与 intake queue 的 chunk 容错同语义；
   batch 级簿记失败（payload 损坏、revision 被删）→ `failed` + error_message。
6. 游标推进时 `jobs_created` 重算为 `count_jobs_in_run`（自愈，崩中游标的已提交
   chunk 靠 dedup 幂等吸收），并同步 `runs.created_count` /
   `runs.status='completed'` / `error_message` 摘要。

**runs.status 纪律**：异步 run 的 runs 行只取 `created`（受理与喂养中）/
`completed`（喂完）/ `failed`（batch 失败）三值——绝不取 `queued`/`processing`，
否则 intake 消费者的 `claim_intake_run`（匹配 queued 或停滞 processing）会把
items run 当 intake batch 认领。feeding 进度由 `batch` 字段与 `created_count`
承担。

## 4. 确定性 run id 与盲重试

异步受理的 digest payload 与同步路径逐字节同形（`workflow_key` / `source_kind=
"items"` / 原始 `items` / `node_config`），同一提交两模式同一 run id。受理 =
`create_run` upsert + `run_batches` upsert：

- 首次：`create_run(status='created')` 建行 + batch `pending`。
- 盲重试（batch 在途/暂停/完成且未枯竭）：upsert 幂等 no-op，返回同一 202 句柄。
- 上次 `failed`：run 行由 `RUN_UPSERT_CONFLICT` 的 failed→created 臂治愈，batch
  行由 guarded UPDATE 重置（cursor 归零、计数清零、回 `pending`）——重喂全程经
  dedup 探测过滤已建 job，等价 intake 的 resubmit 语义。
- `completed` 但 job 被部分删除（live 数 < `jobs_created` 记录值）：同一个原子
  guarded UPDATE 重置重排队（`requeue_completed_run_if_depleted` 先例）。
- job 写入的并发认领仲裁不变：`create_jobs_bulk` 的 ON CONFLICT ownership
  （先写者赢）是最终防线，feeder 与并发同步提交竞速同一 item 时输家自然落空。

## 5. 边界与非目标

- 异步模式不改变每 run 条目上限：`workflows.max_items_per_run` 照常约束提交
  （它同时钳制水位）；超出请分批提交。Campaign 的 manifest/桶上传通道不属于
  本设计（产品实体不复活）。
- cancel 只停 feeder，不取消已建 job；后者走既有 job 批量操作（其异步化是
  #946 的独立切片）。
- EXEC-GENERATION-002：本设计不产生新的五表（jobs/job_nodes/node_runs/
  executor_leases/agent_execution_requests）写面、产物字节写面或清单行写面——
  job 创建全部经 `JobCreationPipeline.insert_jobs`（已登记
  `server/app/jobs/queries/job_bulk_sql.py`），`run_batches`/`runs` 簿记不属于
  注册表钉住的三族，故注册表无需新增条目（新增反而被 stale-entry 检查拒绝）。
- Studio UI 不变（异步提交接线属后续切片）；对外 API 行为新增（`mode` 字段 +
  202 + batch 句端点），api.ts 经 `export_openapi` 机械重生成。
