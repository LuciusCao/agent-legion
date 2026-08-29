# 前后端性能与代码质量 Review（origin/develop @ f9bc9456）

> **落地状态（2026-08-26 补注）**：§六落地顺序的前 7 项（前端 #1/#4/#5、
> 后端 P0-1/H2/H1 及前端 #2 的行组件 memo 等）已随 PR #179（`0d18c671`）
> 落地：jobs(run_id) 索引（schema v59）、事件循环上的 gzip 压缩降档、
> 4 处 async 路由的同步 DB auth 下放线程池、material TTL sweep 的 S3
> 调用移出事务、connection_tokens 行锁改 advisory lock 单飞、App.tsx /
> WorkspaceLayout 字段级 selector、Job 列表行 memo 化等。本文其余内容
> 是时点快照，`path:line` 证据反映审查时代码；当前状态以代码与
> CHANGELOG 0.3.0 起的对应条目为准。

Review 于独立 worktree `.worktrees/perf-review`（分支 `review/perf-quality`）。
方法：4 个并行深度排查（后端 async 阻塞 / DB 查询模式 / 后端质量 / 前端性能与质量）+ 静态工具（ruff、mypy、tsc、eslint 全绿）+ 对全部 P0/高严重度发现逐条人工核验代码。

规模：后端 Python ~55k 行（server/worker/shared/workflow_nodes），前端 TS ~78k 行（含 11k 生成代码），Rust ~10.5k 行（velites，本次未深入）。

总体结论：代码库异常干净（裸 except / 可变默认参数 / utcnow / shell 注入全部为 0，静态检查全绿），
问题集中在**少数高杠杆的性能结构性问题**与**跨线程共享状态无锁**，绝大多数修复面很小。

---

## 一、后端 — 性能

### P0-1 `jobs.run_id` 无索引，5 条热路径全表扫描 ⚠️ 影响最大、修复最便宜

- schema 共 56 个索引，`jobs` 上 8 个组合索引，**没有任何一个以 `run_id` 开头**（`server/app/db/postgres_schema.sql:130` 列定义，440-470 索引清单）。
- 受影响查询（jobs 130k-260k 行，seq scan 反复触发）：
  - `server/app/jobs/queries/batch_queue_sql.py:17` — run 创建/intake upsert 的 `not exists (...)` 子查询，**高频写路径**
  - `server/app/jobs/queries/batch_queue.py:14` — `count_jobs_in_run`，每次 run 轮询
  - `batch_queue_sql.py:29,31` — `RUN_REQUEUE_DEPLETED` 两个相关子查询
  - `server/app/jobs/queries/batch.py:67,90` — delete/count
- 修复：`create index on jobs(run_id)`（一条索引修 5 处）。

### P0-2 遗留 job 列表端点无 LIMIT + `select *` 拉大列

- `server/app/jobs/queries/job_nodes.py:116`：`select * from jobs{where} order by created_at desc` 无 limit，含 `workflow_definition_snapshot_json`、`input_json` 等 KB 级 TEXT 列。
- 路由 `GET /workspaces/{id}/jobs`（`server/app/routes/jobs.py:38` → `job_queries.py:137`）仍在注册挂载（`job_route_group.py:55`），但**前端已全部走 `/jobs/snapshot` 分页端点**（`frontend/src/api/jobSnapshot.ts:15`），此端点仅剩 API 兼容用途。
- 修复：改走已存在的 `list_jobs_paginated` 或直接 410。

### P0-3 material TTL sweep：事务内 S3 网络调用 + 逐行事务 + N+1 无索引 JSONB 守卫

- `server/app/services/material_ttl.py:80-85`：每个 material 一个 `write_transaction`（可改单条批量 UPDATE）。
- `:111-139`：`collect_expired_materials` 每候选一个事务，事务内含 `select for update`、JSONB 全扫、**`storage.delete_object`（S3 网络调用，含重试）在事务内**、单行 delete。S3 调用期间持有写事务（锁 + pool 连接 + 阻塞 vacuum）。
- 守卫查询 `input_json::jsonb->>'material_id' = %s`（`materials.py:288-291`、`material_bundles.py:247-249`、`material_ttl.py:121-125`）对 workspace 全部 jobs 逐行解析 JSON，且**无表达式索引**；TTL sweep 循环内每个 material 重复一次。
- 修复：S3 删除移到提交后（repo 内 `cleanup_sweep.py` 已有此纪律）；expire 合并单条 UPDATE；加表达式索引。

### P0-4 token usage workspace 聚合：一次面板请求 6 次独立连接/查询重复扫同一切片

- `server/app/services/token_usage_workspace.py:337-355`：`build_workspace_usage_response` 串行调用 6 个函数，各自 `job_db.connect()` 独立连接；`node_runs join jobs` 全量 COUNT 2 遍（`_count_workspace_runs` 为 O(workspace 全部 node_runs) 的 count(*)）。
- 修复：合并为 1-2 条 SQL（grouping sets），run 计数改近似/计数表。

### P1 后端性能（摘要）

- `agent_broker/sweepers.py:46-120`：`sweep_expired_claims` 单个大事务内 N+1 lease 查询 + 每行 4-6 条单行 UPDATE（表 660k 行）；建议 `= any(%s)` 批量预取 + 状态合并写 + 分批提交。
- `services/job_deletion.py:128-147`：事务内 `shutil.move` 整个 job 目录（GB 级、跨 FS 为拷贝）；建议行删除与 FS move 分离。
- `jobs/atomic_mutations.py:142-159,195-206`：rerun/replay 写路径逐节点单条 UPDATE（同文件其他分支已用 `in (...)` 批量，风格不一致）。
- `services/artifact_store.py:139-158`：GC 用 `count(*)` 做存在性检查（应为 `exists/limit 1`），循环内每 hash 3 查询。
- `services/quality_replays.py:135,341-346,400-407`：GET 列表走 `write_transaction`（为 lazy reconcile 拿写锁）；`_copy_artifact_refs` 逐 ref 独立写事务。
- `jobs/queries/job_filtering.py:64-68,90-145`：facets 一次调用 5 次全 workspace 聚合扫描；search 4 列 ILIKE 无 trigram 索引。
- `services/ops_metrics.py:78-139`：每分钟 2 次全表 count（claimed 态无部分索引）+ 3 个 group-by。

---

## 二、后端 — async 阻塞（事件循环 / 连接池 / 线程池）

> 部署为**单 uvicorn worker**（`Dockerfile:38`），只有一个事件循环；SSE/WS 心跳全靠它。

### H1 `get_token` 行锁事务内做同步 HTTP 凭证交换 → 连接池耗尽向量

- `server/app/services/connection_tokens.py:73-115`：`write_transaction` 内 `select ... for update` 后调 `adapter.authenticate(config, secrets)` — 阻塞式 `requests.post`（超时 10s，user_login 两步最多 20s，`connection_adapter_user_login.py:104,123`），连接在 HTTP 返回前不归还。
- 并发刷新同一 key 时各自占一个 pool 连接在行锁上排队（连接池上限 32）；耗尽后其余消费者 `getconn` 挂 10s。
- 修复：HTTP 交换移出行锁/事务（advisory lock + 两阶段，或 side-table 去重）。

### H2 4 个 async 路由在事件循环上直接跑同步 DB 调用

- `server/app/routes/agent_workers.py:235` `result()` — `authorize_worker` 每次结果上报都在循环上做 DB 读 + 限流写（每次心跳级调用）。
- `server/app/routes/agents.py:41` `agents_ws()` — 每条 WS 连接 auth 在循环上（对照：MCP 挂载已正确用 `anyio.to_thread`，`mcp_server/http_app.py:56`）。
- `server/app/routes/studio_chat.py:152` `session_events()` — SSE 流建立前 `service.get_session()` 在循环上。
- `server/app/routes/artifacts.py:44`（已弃用路由，影响小）。
- 修复：包一层 `run_in_threadpool`。后果：连接池紧张时每次调用变成最长 10s 的循环挂起，所有 SSE/WS/仪表盘流一起停。

### H3 GZip level 9 在事件循环上压缩大 JSON

- `server/app/http_middleware.py:48`：`add_middleware(SelectiveGZipMiddleware)` 未传 `compresslevel`（starlette 默认 9），`http_gzip.py:34` 在 async send 路径同步压缩。大 JSON 响应（job 列表/统计）每响应占循环数百 ms。已压缩类型豁免已做（好），但普通 JSON 没有。
- 修复：`compresslevel=6`。

### M 级（摘要）

- 全部 sync 路由 + MCP 回环共享 anyio 默认 40 线程池：并发聊天会话（上限 32）+ 卸载操作共享容量，线程饱和 = 全 API 延迟。
- `studio_chat/acp_session.py:101-103`：每条流式 chunk 的回调直接在会话循环上执行一次 `write_transaction`（每 token 一次 DB 往返；好在是会话私有循环，不碰 server 循环）。
- `acp_session.py:261`：每个空闲聊天会话挂起一个线程池线程（`await asyncio.to_thread(queue.get)`），每会话线程成本×2（权限等待再+1）。
- `main.py:165,194`：lifespan 里同步 `subprocess.run`（10s 超时）与 DB 写在循环上（仅启动期）。

---

## 三、后端 — 代码质量 / anti-pattern

### 严重

1. **`worker/registration_retry.py:50`** — `retriable=(Exception,)`：把 TypeError 等代码 bug 当作“Host 暂不可用”无限重试，bug 被伪装成网络问题，且只 print 一行无 traceback。
2. **`server/app/events/bus.py:52-58`** — `InProcessEventBus.publish` 在 `loop.is_running()` 检查与 `call_soon_threadsafe` 之间有竞态窗口（loop 关闭瞬间 RuntimeError 未捕获，沿 route 线程上抛 500）；docstring 声称线程安全与实现不符。`attach_loop` 前 `_send` 在调用者线程内联改 `_subscribers`，与循环线程的 subscribe 并发无锁。
3. **`events/bus.py:80,96`** — 两处 `contextlib.suppress(Exception)` 包队列驱逐：异常被静默吞掉，订阅者可能既收不到 `_EVICTED` 也未被移除 → 僵尸 SSE 连接。

### 重要（摘要）

- 全局可变状态无锁：`services/node_codes.py:69-71`（`_publish_generation += 1` 非原子）、`services/agent_service.py:26`（`_published_cache` 无锁且**无容量上限**，workspace 删除后条目永久残留）、`skills/runtime.py:22`（`_version_memo` 无锁无上限）。对照：`db/pools.py`、`events/agents.py` 等处有锁版本是好的。
- 错误路径丢 traceback：`studio_chat/acp_session.py:256`（主失败路径 `logger.warning` 无 `exc_info`）、`acp_session.py:216`（kill 失败被 suppress）、worker 6 处 `print(f"...{exc}")`（`upload_queue.py:235,253,293`、`execution_lifecycle.py:47`、`metrics_cache.py:112`、`status.py:101`）。
- 8 个 >150 行函数（`routes/agent_workers.py:38` 243 行、`main.py:63` 220 行、`agent_broker/claim_evaluate.py:34` 213 行等）；god module：`studio_chat/service.py`（563 行 30+ 方法）、`workflow_draft_compare.py`（547 行）。
- 类型：`Any` ~1802 次，服务层 API 边界几乎全 `dict[str, Any]` 充当 DTO（Top：connections.py 25、token_usage_workspace.py 22、node_config.py 22）——mypy 全绿是因为“从未检查”而非“绕过检查”。
- `workflow_worker/mark_scan.py:41-43`：唯一不做 tzinfo 归一化的 `fromisoformat`，naive 值会与 aware 兜底相减抛 TypeError（当前靠 row factory 渲染规避，隐性契约）。

### 次要（摘要）

- 21 处生产 `assert` 做运行时校验（`python -O` 下消失；`worker/runtime_controls.py:38` 用 assert 校验**外部输入**最该改）。
- 重复样板 Top3：`try/except JobServiceError → raise_job_http_error` **103 处遍布 27 文件**（一个 exception handler 可全消）；DB 时间戳解析 helper 重写 ≥6 遍（顺带修 mark_scan）；workspace-scoped CRUD endpoint 三件套逐字重复多份。
- `routes/__init__.py:58` `create_router` ~30 参数的 god factory。

### 已验证的良好实践（勿改）

触发器维护的 `workspace_job_status_counts` 聚合、`cleanup_sweep.py` 的 keyset+超时+FS 事务外纪律、`job_pagination.py` keyset 游标、`_job_rerun_batch.py` 批处理范式、SSE 有界队列+驱逐哨兵、所有 `requests` 带 timeout、后台线程全 daemon+stop/join。

---

## 四、前端 — 性能

### P0 事件驱动的整树重渲染

1. **`src/App.tsx:7`** — `const { connectAgentsWs } = useAgentsStore()` 无 selector 订阅整个 store；每条 WS agent 消息（`agentsStore.ts:50` `set({agents: upsertAgent(...)})` 新数组）→ App 重渲染 → 整棵路由树重渲染。**全站最大放大器，一行修复**：`useAgentsStore((s) => s.connectAgentsWs)`。
2. **Job 列表行链零 memo + 内联闭包** — `JobListVirtualized.tsx:55` `onToggleSelect={() => onToggleSelect(jobId)}` 击穿 memo；`JobListVirtualRowById/JobListVirtualRow/JobListItem` 全部未 `React.memo`；每个 SSE `job_patch_batch` 事件 `{...oldJobsById}` 新引用 → ~20 可见行全部重渲染；`JobListItem.tsx:44-81` 每行多次 `.find/.filter`。且 store patch 无 batching（`workspaceEventHandlers.ts:41-49` 逐事件 apply）。
3. **Studio 全页 context value 每渲染新建** — `useWorkflowStudio.ts:51-89` 返回未 memo 的新对象 → `<StudioStateContext.Provider value={studio}>`（`WorkflowStudioPageContent.tsx:31`）→ **YAML 每敲一个字全 Studio 重渲染**（Canvas/Chat/Layout/Dialogs 全部）；chat 每条 SSE 消息同理。叠加 `useStudioDag.ts:9-15` workflow 引用随草稿变 → ReactFlow 全节点换引用。
4. **`WorkspaceLayout.tsx:20,24-27`** — 无 selector 订阅 uiStore/agentsStore：toast/标题等任何字段变化 → 页面骨架 + `<Outlet/>` 整子树重渲染。同模式：`NodePanel.tsx:29`、`SettingsPage.tsx:38`。
5. **`JobDetailPage.tsx:86-119`** — effect 把 JSX 存进 uiStore（`setDetailPageActions(<JobDetailActions .../>)`）+ 5s 轮询 → running 期间每 5s 一次全 layout 重渲染循环（与 #4 叠加）。

### P1 渲染路径昂贵计算（摘要）

- `StudioChatMessageList.tsx:60-72`：O(messages×toolCalls) 的 render 内查找无 useMemo，流式热路径每条消息全量重算。
- `JobProgressPanel.tsx:78-84`：派生计算无 useMemo + 每 run 一个独立 useQuery（`TokenUsageRunDetail`），N 个观察者随状态翻转各 refetch。
- `DagGraph.tsx:204-257`：hover 一个节点 = 全节点/边对象重建（DagNode 有 memo 但 props 引用全变）。
- `RichText.tsx:15-34`：无 memo，每次渲染重跑 DOMPurify sanitize + LaTeX 提取（多个列表每项都用它）。
- `JsonTree.tsx:45-53`：默认全展开 + `collapseObjectsAfterLength={Infinity}` → 大 JSON 产物一次渲染全部 DOM。
- `AgentDefaultsSection.tsx:33` 等：render 内 `JSON.stringify` 每键一次。

### P2 轮询与请求（摘要）

- 轮询本身设计好（条件轮询、`refetchIntervalInBackground` 默认 false、queryKey 正确、定时器全清理）；一个例外：`AgentWorkerStatusList.tsx:20` 15s 轮询挂在常驻 popover（CSS hover 显隐）→ 所有页面每 15s 白拉一次，建议按 hover enable。
- `patchActions.ts:65`：每 patch 批次 `{...oldJobsById}` 克隆全部已加载 job（首页 500 条）→ 高频 GC 压力。

### P3 Bundle

- `vite.config.ts` manualChunks 兜底 `vendor` 桶把 uplot/marked/react18-json-view/dompurify 全打进同一 chunk，且 `main.tsx:7` react-query 也在 vendor → **首屏加载整个 vendor**，抵消了已做好的全页面 `React.lazy`。建议为重库独立 chunk。katex CSS eager import、55 图标集中 import 同理。

### 前端良好实践（勿改）

`@tanstack/react-virtual` 虚拟化 + loadMore 防重、selector 引用稳定性（模块级缓存 facets/makeSelectNodeOptions）、uplot 增量 setData、`StudioChatTextBubble` markdown memo、全页面 React.lazy。
（另：`SubtitlePanel/TimelineStrip/VideoPlayer` 当前无消费者，疑似 dead code。）

---

## 五、静态工具结果

- `ruff check`：All checks passed（520 files）
- `mypy server/app`：no issues（520 files）
- `tsc --noEmit`：通过；`eslint src/`：通过

---

## 六、建议落地顺序（性价比排序）

1. **前端 #1**：App.tsx selector 一行 → 消除全站最大重渲染放大器
2. **后端 P0-1**：`create index on jobs(run_id)` 一条索引修 5 处热路径
3. **前端 #4/#5**：WorkspaceLayout/JobDetailPage selector 化 + actions 不走 uiStore
4. **后端 H2**：4 处 async 路由 auth 包 `run_in_threadpool`
5. **前端 #2**：Job 列表行组件 memo + 传 jobId 不传闭包
6. **后端 H1**：get_token 的 HTTP 交换移出行锁事务
7. **后端 P0-3**：material TTL sweep 的 S3 调用移出事务
8. **前端 #3**：Studio context 拆分（改动面最大，可放后）
9. 其余 P1/P2/质量问题按上文清单逐项消化

—— 以上每条高严重度发现均已人工核验源码；如需逐项开 fix 分支，从顺序 1-4 开始收益最高。

---

## 附：2026-08-26 优化落地记录（同一 worktree，分支 review/perf-quality）

按「建议落地顺序」完成了 11 项修复，`./scripts/check-quick.sh` 全绿（backend 3621 tests + frontend 1373 tests + rust 全部通过）：

### 后端
1. **jobs(run_id) 索引（schema v59）** — DDL 放在 v59 专属 migration `db/migrations/jobs_run_id_index.py` 而非 schema.sql：init_db 升级旧库时先重放全量 DDL 文件、再跑数据迁移，v52 形状的库此时 `jobs` 上还叫 `batch_id`，schema 文件里的索引会引用尚未存在的列（`test_v52_database_upgrades_via_init_db` 抓住了这一点）。放 v53 `migrate_runs` 也不行：v53–v58 的库会跳过该迁移（`53 <= max_applied`），索引永远建不上（`test_v58_database_upgrades_gain_the_index` 回归测试抓住了这一点，需 `@pytest.mark.fresh_schema` 避免污染共享 schema）。registry 加 v59 条目（带 apply fn），pin 测试在 `tests/db/test_jobs_run_id_index.py`。
2. **gzip compresslevel=6**（`http_middleware.py`）。
3. **4 个 async 路由的同步 DB auth 卸载到线程池**（`agents.py` WS、`agent_workers.py` result、`studio_chat.py` SSE、`artifacts.py` 上传）。
4. **material_ttl**：expire 合并为单条批量 UPDATE；S3 删除移到事务提交后（失败降级为孤儿对象，交给 bucket lifecycle 兜底，不再持有写事务做网络 IO）；对应测试语义同步更新。
5. **connection_tokens 单飞刷新**：`for update` 行锁 → `pg_advisory_xact_lock`（连接 key 级），HTTP 交换期间不再锁 `external_connections` 行；single-flight 语义保留（并发只交换一次）。service-data-boundary 基线 +1（advisory lock 语句是等价替换，非新增数据访问路径）。
6. **质量修复**：`registration_retry` retriable 收窄为 `requests.RequestException`（代码 bug 不再被伪装成网络重试）；`events/bus.py` publish 的 loop 关闭竞态加 RuntimeError 兜底；ACP session 主失败路径补 `exc_info`。

### 前端
7. **App.tsx / WorkspaceLayout selector 化**（全站两个最大重渲染放大器，各一行/数行）。
8. **JobDetailPage actions 签名化写入**（status+updated_at+nodes+loading 签名控制 uiStore 写入，消除 5s 轮询的全 layout 重渲染循环）。
9. **Job 列表行组件 memo 链**：三个行组件 `React.memo` + `onToggleSelect` 改传 jobId（消灭内联闭包）+ `virtualRow` 换 `virtualRowStart` 数字 prop。
10. **渲染热点 memo 化**：RichText（sanitize+LaTeX）、StudioChatMessageList（O(n·m)→Map 查找 + MessageItem memo）、JobProgressPanel（useMemo）；JsonTree 默认折叠 2 层；vite chunk 拆分（marked/json-view/uplot/dompurify 独立 chunk，不再全进首屏 vendor）。

### 过程中发现并处理的次生问题
- 5 个 migration pin 测试断言 v58 name → 更新为 v59。
- `test_registration_retries_transient_host_errors_without_traceback` 原用 `urllib.error.URLError`（真实 Client 走 requests，永不抛它）→ 改用 `requests.ConnectionError`。
- 新 db 测试文件需登记 `tests/conftest.py::_POSTGRES_TEST_FILES`。

### 结构性防线：schema 升级 parity 测试（`tests/db/test_schema_upgrade_parity.py`）

针对「init_db 先重放全量 DDL、再跑 version>max(applied) 迁移」这条无强制机制的放置规则，新增按**失败类别**设防的测试：构造 SCHEMA_VERSION-1 形状的库（scratch schema，与 worker 主 schema 隔离）走 init_db 升级，与 fresh 库做全 catalog（columns + indexes）比对，二者必须完全一致。已做有效性自证：把索引放回 migrate_runs（第二版错误）时测试精确报出 `only fresh: jobs.idx_jobs_run_id`；放 schema.sql（第一版错误）由既有 `test_v52_...` 覆盖。未来任何版本把 DDL 放错边都会在此变红，`test_newest_migration_undo_inventory_is_current` 会在版本 bump 时提醒扩展 undo 清单。

该测试首跑即抓到一个**现存**偏差：`job_batches` / `workspace_executor_allocations` / `workspace_node_bindings` 三张退役表会被 schema 文件重建（供旧数据迁移重放用）却只在各自的退役迁移里 DROP——v48–v58 的升级库跳过那些迁移，三张空表永久遗留（fresh 库没有）。已在 `init_db` 迁移链末尾加幂等清理 DROP 修复（与既有 cms_config_json 清理同位置）。
- 架构预算 ratchet：4 个文件行数 ceiling 经 `ratchet_architecture_budgets.py --bump` 合法上调；JsonTree ceiling 随默认 ratchet 回落（63→61）。
- `App.test.tsx` 的 store mock 需支持 selector 调用形态。

### 更正：预算上调已被后续 commit 撤销（`e6e32d55`）

上文的「4 个文件 ceiling 经 `--bump` 合法上调」按 AGENTS.md「超出体积预算的文件必须拆分或回退，不能手动抬高 ceiling」判为违规（codex review P1）：`e6e32d55` 将 5 个被抬高的 ceiling 全部恢复原值（JobDetailPage 201→184、StudioChatMessageList 215→198、studio_chat 194→183、connection_tokens 208→186、material_ttl 183→170），改为拆分超预算文件——`useJobDetailActions.tsx`（actions 推送 hook）、`StudioChatStatusLine.tsx`、`connection_token_legacy.py`、`material_ttl_sweeper.py`——新模块按 ratchet 规则以 actual+10 登记，service-data-boundary 基线同步收录（`material_ttl.py` 条目随后按「只降不升」纪律下调 10→9）。

同期系统 review 还发现并已修复：上表第 8 条的签名门控在原始实现里是 no-op（effect deps 同时列了 `actionsSignature` 与原始 `detail`/`nodeCatalog`，签名从未起作用）——`useJobDetailActions` 改为 ref 快照读取、effect 仅以签名为触发条件；第 10 条的 `MessageItem` memo 被整体 `props` 透传击穿——已拆为逐字段 props，memo 恢复生效。

### 未在本轮处理的（后续 PR 候选）
- Studio context 拆分（改动面大，报告 P0-3）。
- token usage 6 查询合并、sweep_expired_claims 批量化、job_deletion 事务内 shutil.move（报告 P1 项）。
- route 层 103 处 try/except 样板收敛为全局 exception handler。
- `agent_service._published_cache` 无上限等次级质量问题。
