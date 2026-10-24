# 批量操作异步化（#946）

日期：2026-10-21
状态：现行（0.8.1；runs-async milestone Phase 4）

9 个批量 job 端点（batch-rerun、rerun-by-failure、DELETE batch、batch-run-to、
batch-pause、batch-resume、batch-upgrade-workflow、package、clear-packed）在大选择
下不再于请求线程内逐 job 执行（线程池槽位被长请求占用是调研 #M39 确认的真实痛点），
而是秒级受理返回 202 + 任务句柄，由进程内 feeder 按游标滴灌执行。小选择保持同步
快路径，响应逐字节不变。本设计是 [runs-async-mode.md](runs-async-mode.md)（#1117）
基建向「变更既有 job」族的映射：目标操作从 create 换成 rerun/delete/upgrade 等。

## 1. 请求契约

- 显式 `job_ids` 仍是请求线程的直通选择；`filter` / `exclude_ids` 与 rerun-by-failure
  的「全部匹配失败」空选择只先读取至多阈值+1 个 id 来决定同步/异步，不再有单请求
  选择数上限或 `batch_selection_too_large` 422。空选择 400/空结果语义不变。
- 大 filter/failure 选择的 `target_spec_json` 冻结原始筛选参数、keyset 上界与有界首
  buffer，不冻结全量 id。feeder 后续按游标增量补页；受理后晚到、排序在上界之前的
  job/failure 不会混入。jobs 上界为 `(created_at,id)`，failure 上界为
  `(finished_at,node_run_id)`。
- `len(ids) ≤ BATCH_OPS_ASYNC_THRESHOLD`（默认 1000，env
  `AGENT_LEGION_BATCH_OPS_ASYNC_THRESHOLD` 覆盖、重启生效；与 #1157 phase0/phase3 的
  1000 对齐）：走既有同步路径，200 + 原响应体，行为不变。
- 超过阈值：202 `JobBatchOperationAcceptedResponse` = `{operation}` 句柄（id、kind、
  status、items_total、计数器、时间戳）。OpenAPI 层面 200 保持原 $ref，202 单独声明
  （export gate 拒绝 inline union schema）。
- 轮询：`GET /workspaces/{id}/job-batch-operations/{op_id}`（状态 + 计数 +
  `reason_counts` 失败/跳过原因分布 + `output` 终态产物，如 package 的
  package_filename/download_url）；`GET .../results?limit&cursor`（keyset 分页的逐
  job 结果，`result` 载荷与同步端点的逐 job 结果 dict 同形）。跨 workspace 引用一律
  404。pause/resume/cancel 控制面本期不外露（状态机预留 paused/cancelled）。

## 2. 状态行（schema v101）

```
job_batch_operations(
  id text primary key,                        -- uuid4（非确定性：批量操作无去重语义）
  workspace_id text not null references workspaces(id) on delete cascade,
  kind text check(kind in ('rerun','rerun_by_failure','delete','run_to',
                           'pause','resume','upgrade_workflow','package','clear_packed')),
  status 状态机（与 run_batches 同形：pending/running/paused/completed/failed/cancelled),
  target_spec_json,   -- 显式 id；或有界 buffer + filter/failure + freeze/next cursor
  next_index, items_total,  -- 流式选择的 total 是已发现数，随补页增长，终态精确
  succeeded_count/skipped_count/failed_count,  -- 每次推进按结果子表重算（自愈）
  output_json,        -- kind 级终态产物（package）
  chunk_errors_json（至多 20 条）, error_message, created_by, 时间戳
)
job_batch_operation_results(
  op_id references job_batch_operations(id) on delete cascade,
  seq integer,        -- 冻结列表下标；(op_id, seq) 主键兼任重放护栏与分页序
  job_id, status check(succeeded/skipped/failed), reason_code, message,
  payload_json        -- 同步响应逐 job 结果 dict 原文（node_key/rerun_nodes/upgrade 计数…）
)
```

受理时的 id 归一化按 kind 对齐同步路径的消费方式：rerun/delete/upgrade/package/
clear-packed/rerun-by-failure 去重 + strip（同步路径本就如此）；run_to/pause/resume
保留原始列表（同步路径逐条循环、不去重，重放语义靠结果表 seq 对齐）。

## 3. Feeder

`JobBatchOpsFeeder`（`services/job_batch_operations/feeder.py`）逐条对应
RunBatchFeeder 模式：SKIP LOCKED 认领（停滞 600s 回收）、每 tick 一个 chunk
（`JOB_BATCH_OPS_CHUNK_SIZE=200`）、非末次推进回 `pending`、pause-wins CAS、
消费循环 `consume_job_batch_operations`（`asyncio.to_thread` 隔离 + 失败退避）由
`startup_tasks.BackgroundTasks` 托管。**明确不引入进程池**（瓶颈在 DB 往返）。

与 runs feeder 的关键差异：

- **流式补页**：首 buffer 至多阈值+1 条，后续每 tick 至多 200 条；水位闸在补页之前，
  背压时不继续扩大选择。补页后的 target spec、已发现 `items_total`、结果计数和
  `next_index` 由同一个 pause-wins CAS 推进，feeder 不再每 chunk 解析全量 id JSON。
- **重放护栏**：rerun/package 等变更天然不幂等（无 dedup 探测可吸收重走），所以
  结果按 chunk 先于游标落子表，chunk 起步先探 `done_seqs`、已记录 seq 绝不重执行；
  `(op_id, seq)` 的 ON CONFLICT 是最终兜底。崩溃发生在结果写入与 CAS 之间时，旧
  target spec/cursor 重取同页，done seq 只补簿记。残留窗口 = 单条语句级别的「变更已提交、
  结果行未落」（intake 消费者 committed-jobs-stay 同款接受度）。
- **逐 job 执行 = 调同步路径的同一批 service 入口**（`batch_rerun` /
  `batch_delete` / `batch_run_to` / `batch_pause` / `batch_resume` / `batch_upgrade` /
  `clear_packed_status` / `rerun_by_failure_category`，chunk 显式 ids 直穿），所以逐
  job 结果语义与写前校验纪律（lease_guarded_mutation 等 EXEC-GENERATION-002 已登记
  写面）由构造保证不变。chunk 级意外异常归一化为该 chunk 逐 job 的
  `failed/internal_error` 结果（#759 P1 先例），不丢任何一个选中 job 的判定；簿记级
  失败（target_spec 损坏等）→ `failed`。
- **水位闸只对产生执行负载的 kind**（rerun / rerun_by_failure / run_to / resume /
  upgrade_workflow——clean 升级把 job 翻 queued、节点全重置 pending，与 rerun 同款
  负载）：与 runs feeder 相同的 workspace backlog 水位（`min(30000, max_items_per_run)`）；
  delete/pause/package/clear-packed 不制造 backlog，不闸。
- **package 是批级操作**：chunk 内只做逐 job 资格判定（结果即同步路径的
  eligibility verdicts），游标耗尽时由完成钩跑一次打包（重取 succeeded ids 的 live
  job 行再校验 completed），zip 文件名/下载 URL 落 `output_json`；重放时
  output 非空即复用，不重复打包。

## 4. 边界与非目标

- 单请求选择上限与 `batch_selection_too_large` 已退役（#1362）；
  `AGENT_LEGION_MAX_BATCH_JOBS` 在 0.8.2 仅打 deprecation warning、不再影响行为。
  在途执行负载由 backlog 水位约束；任务中心的 pause/cancel 仍是状态机预留，尚无公开
  控制端点，不把「可取消」当作当前已交付的护栏。
- 前端进度呈现（轮询 + 结果汇总替换一次性 toast）是独立的 UI 变更切片，按 0.8.1 的
  原型先行纪律走用户验收；后端合并不依赖它，但 >1000 选择的 UI 消费需前端跟进
  （202 句柄的轮询接线）。
- EXEC-GENERATION-002：两张新表是批量簿记，不属于注册表钉住的三族写面（执行态 /
  产物字节 / 清单行）——run_batches 同论证，注册表无需新增条目。kind 词表的规范
  字符串住在 queries 模块（DDL 旁），services 层只引用常量（BOUNDARY-DATA-001 的
  SQL 关键字扫描约束）。
- runs-async 的确定性 run id / 盲重试幂等不适用于批量操作（同一选择重复提交 =
  两个任务，各自幂等推进；pause/delete 等天然幂等，rerun 重跑语义与用户手工重跑
  一致）。
