# workflow_key 退役盘点（issue #211 Phase 1）

状态：盘点文档（Phase 1 产出，不含代码/契约变更）
基准：develop@bfd7ad4f（schema v64）· 分支 `refactor/211-workflow-key-inventory`

## 概述

自 schema v61/62（invariant `DB-WORKSPACE-KEY-BINDING-001`）起，workspace id 与
workflow key 是同一个标识：创建时显式提供 id（即 `default_workflow_key`，约束
`^[a-z0-9][a-z0-9_-]{0,63}$`），终身不可变。v62 迁移
（`server/app/db/migrations/workspace_id_key_binding.py`）已把存量 workspace 的 id
改成已绑定的 key（空 key 按 id 回填），使不变式对历史行同样成立。

但 key 仍作为独立概念散落各处：后端非测试代码 `workflow_key` 词匹配约 598 处
（106 个文件，其中 migrations + schema 98 处）、`default_workflow_key` 59 处；
前端非测试代码 `workflowKey` 63 处、`workflow_key` 77 处（含生成物
`frontend/src/generated/api.ts`：camel 3 + snake 54）。双写意味着每个新功能都要
回答「用 id 还是 key」——两者恒等，这是纯债务概念。本文按四类穷尽列出全部引用，
作为 Phase 2-4（契约下线 → DB 列/组合 id 迁移 → 删列收尾）的执行依据。

盘点方法：`rg -n "workflow_key|workflowKey|default_workflow_key"` 限定
`server/app`（排除 `*test*`）与 `frontend/src`（排除 `*.test.*`），DB 侧人工核对
`postgres_schema.sql` 全部列/索引/触发器与 migration 链。文中所有行号基于上述
基准 commit，可用同一 grep 复核。

## 类别 1：API 契约字段（Pydantic models）

### 响应字段（下发）

| 位置 | 字段 | 消费方 | 处置建议 | 风险注记 |
|---|---|---|---|---|
| `server/app/routes/job_contracts.py:101` `WorkspaceStatsResponse` | `workflow_key: str` | 前端 `WorkspaceMainPage`、rerun、onboarding 全链 | 需契约协调（Phase 2 首批）：标 deprecated，前端改读 `workspace_id` | 后端 `workspace_stats.py:15-17` 空分支 fail（`InvalidOperationError`）；v62 后 key 恒非空，空分支是防御残留 |
| `server/app/routes/job_view_contracts.py:18` `JobSummaryResponse` | `workflow_key: str` | 前端 job 列表/详情、`workflowNodes.ts` 节点匹配 | 需契约协调 | 前端 `deriveJobDetailPresentation.ts:8-9` 直接当 label 用 |
| `server/app/routes/run_contracts.py:44` `RunRecord` | `workflow_key: str` | 前端 run 列表 | 需契约协调 | 值恒等于 workspace_id |
| `server/app/routes/workflow_revisions_contracts.py:9` `WorkflowRevisionSummary` | `workflow_key: str` | Studio、draft compare | 需契约协调 | revision 行从 workspace key 派生 |
| `server/app/routes/workspace_contracts.py:12` `WorkspaceRecord` | `default_workflow_key: str` | 前端 workspace 详情/Dashboard 兜底 label | 需契约协调（最后一批，依赖列本身退役） | 与 `id` 恒等；`DashboardPage.tsx:20` 作 fallback label |
| `server/app/routes/failed_node_run_contracts.py:12` `FailedNodeRunItem` | `workflow_key: str` | 前端失败分类面板 | 需契约协调 | 来自 jobs 列投影 |
| `server/app/routes/quality_contracts.py:33` `QualitySampleBatch` | `workflow_key: str` | 质量抽样批次 | 需契约协调 | 落库列（见类别 2） |
| `server/app/routes/workspace_execution_contracts.py:26` `WorkspaceAgentRouteEntry` | `workflow_key: str` | 前端 Agent 路由/LocalNodeLimit 过滤 | 需契约协调 | 过滤键（`route.workflow_key === workflowKey`），改用 workspace_id 语义不变 |
| `server/app/routes/workspace_execution_contracts.py:7` `NodeLimitRequest` | `workflow_key: str`（min_length=1） | 节点并发限制 PUT | 需契约协调（请求+响应同形） | 与 workspace_id 组合 PK 的一维 |
| `server/app/routes/studio_agent_context_contracts.py:27` `StudioContextWorkflow` | `workflow_key: str` | MCP studio context | 需契约协调 | MCP 工具面，外部 Agent 可能消费 |
| `server/app/routes/studio_agent_tool_contracts.py:19` `StudioAgentActiveWorkflowResponse` | `workflow_key: str \| None` | MCP 工具 | 需契约协调 | empty 分支**仍可达**（`studio_agent_tools.py:108-110`：workspace 无已发布 revision 时返回 state=empty——创建路径刻意不 seed revision，from-scratch 流程依赖它；Phase 4 只能收紧 workflow_key 可空性，**不得删 empty 分支**，codex on #256） |
| `server/app/routes/agent_workers_contracts.py:129` `AgentClaimResponse` | `workflow_key: str` | Worker claim 协议（外部进程） | 需契约协调（跨进程协议，需 Worker 同步发版） | `worker/code_runner.py:178`、`worker/execution/run.py:136` 读 manifest/claim 的该字段 |
| `server/app/routes/workflow_draft_compare_contracts.py:35` `WorkflowRevisionSummaryItem` | `workflow_key: str` | Studio diff 视图 | 需契约协调 | 同 revision 派生 |
| `server/app/routes/workspace_execution_contracts.py:40` `WorkspaceSettingsPayload` | `workflowKey: str`（required） | 前端 settingStore 快照 | 需契约协调（Phase 2 重点：blob 字段） | settings 快照 blob 往返透传；编辑器已删但仍 required |

### 请求字段（回传）

| 位置 | 字段 | 处置建议 | 风险注记 |
|---|---|---|---|
| `server/app/routes/job_contracts.py:14` `JobBatchRequest` | `workflow_key: str` | 需契约协调（intake 入口） | 前端 `AddItemsDialog.tsx:157` 提交；与 workspace id 恒等 |
| `server/app/routes/run_contracts.py:37` `RunCreateRequest` | `workflow_key: str`（min_length=1） | 需契约协调 | runs API 创建入口 |
| `server/app/routes/job_contracts.py:58` `WorkspaceSettingsSectionRequest` | `workflowKey: str \| None` | 需契约协调 | PATCH section |
| `server/app/routes/workspace_execution_contracts.py:48` `WorkspaceConfigurationSettingsRequest` | `workflowKey: str \| None` | 需契约协调（兼容期容忍回传） | `workspace_configuration.py:160-164` 已只做恒等校验，改值报 400（不可变守卫） |
| `server/app/routes/quality_contracts.py:23` `QualitySampleBatchCreateRequest` | `workflow_key: str \| None` | 需契约协调 | 落库列 |
| `server/app/routes/job_rerun_by_failure_contracts.py:22` `JobRerunByFailureRequest` | `workflow_key: str \| None` | 需契约协调（失败过滤参数） | 透传 `failed_node_runs` 查询谓词 |

### URL 路径参数（契约的一部分，非 Pydantic 字段但同属 API 面）

- `server/app/routes/workflow_node_codes.py`：8 条
  `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code*` 路由
  ——**需契约协调**：URL 里 key 与 workspace_id 双写（值恒等）；MCP server
  （`server/app/mcp_server/server.py:133,144`）与前端
  `WorkflowNodeCodeSection.tsx:19` 硬拼同一路径。
- `server/app/routes/studio_agent_tools.py:104,163`：2 条同形 MCP 路由。
- `GET /api/workspaces/{id}/jobs?workflow_key=`、`GET /api/workspaces/{id}/failed-node-runs?workflow_key=`
  （`server/app/routes/jobs.py:30`、`failed_node_runs.py:41`）——查询参数，前端
  `failureApi.ts:13` 传参。

## 类别 2：DB 列（postgres_schema.sql + migration 链）

当前 schema v64。带 `workflow_key` 列的表（列全部为 `text not null`）：

| 表（schema.sql 行） | 约束/索引角色 | 查询谓词 | 处置建议 | 风险注记 |
|---|---|---|---|---|
| `workspaces.default_workflow_key`（:4） | 无索引，纯列 | 全链解析入口（revision/DAG/stats） | **需 schema 迁移（Phase 4 删列）** | 退役终点；`workspace_configuration.py:199` 仍在 UPDATE 写它 |
| `jobs.workflow_key`（:141） | **6 个索引成员**：`idx_jobs_workflow_status`(:460)、`idx_jobs_workflow_updated`(:463)、`idx_jobs_active_marks`(:468-470)、`idx_jobs_workflow_source`(:471)、`idx_jobs_workspace_workflow_status`(:472)、`idx_jobs_workspace_workflow_source`(:473) | worker 扫描（`job_scan_marks.py:26`、`job_scan_delta.py:27`）、列表过滤、dedup 键（`job_keys.py:20`） | **需 schema 迁移（最重的列）** | 与 workspace_id 组合的索引前缀冗余（workspace_id 已含 key 信息）；扫描查询全部可以只按 workspace_id 走 |
| `runs.workflow_key`（:125） | 无独立索引（`idx_runs_workspace` 只有 workspace_id） | run upsert/查询谓词 | **需 schema 迁移** | run_id 组合串的一部分（见类别 3） |
| `job_batches.workflow_key`（:106） | 历史表（v53 已 harvest 进 runs 后 drop） | 仅历史 migration replay | 保留（迁移考古数据） | `migrations/runs.py:89` drop 前的最后读取 |
| `workspace_node_limits.workflow_key`（:62） | **组合 PK 第 2 列** `(workspace_id, workflow_key, node_key)` | 节点并发限制读写（`jobs/node_limits.py`） | **需 schema 迁移**（PK 重建） | 退役后 PK 退化为 `(workspace_id, node_key)` |
| `workspace_node_routes.workflow_key`（:70） | **组合 PK 第 2 列** | Agent 路由解析（`server/app/workflow_worker/routing.py:66`） | **需 schema 迁移**（PK 重建） | 同上；issue 提到的「capacity 快照的 key 维度」即此类 |
| `workspace_node_capacities.workflow_key`（:79） | **组合 PK 第 2 列** | legacy 投影（publish 时清剪，`workflow_revisions.py:47`） | **需 schema 迁移**（或随 legacy 投影整体退役评估） | 表本身已被 `workspace_agent_capacities` 取代 |
| `workspace_node_bindings.workflow_key`（:54） | 组合 PK 第 2 列 | 无运行时查询（v47 已退役，表保留供历史 migration replay） | 保留（迁移考古） | `migrations/executor_retirement.py` drop |
| `executor_leases.workflow_key`（:202） | `idx_executor_leases_workflow_node_active`(:654) 第 3 列 | lease 容量查询（`executors/_lease_claims.py:54,87`） | **需 schema 迁移** | 索引重建（去 workflow_key 维度） |
| `agent_execution_requests.workflow_key`（:315） | `idx_agent_execution_requests`(:360) 成员 | claim 扫描投影 | **需 schema 迁移** | Worker claim 协议字段同源 |
| `workspace_job_node_status_counts.workflow_key`（:544） | **组合 PK 第 2 列** | 触发器维护（**4 个函数 + 3 个触发器**：`bump_job_node_status_counts`、`sync_job_node_status_counts`、`deduct_job_node_status_counts`、`rekey_job_node_status_counts`，触发器 node_sync / job_deduct / job_rekey，schema.sql :550-651——codex on #256 修正计数） | **需 schema 迁移（触发器全链重写，七个对象逐一处理）** | `rekey` trigger 的 `after update of workflow_key on jobs` 本身就是「key 会变」时代残留；删 jobs.workflow_key 前漏掉任一链路会 DDL 失败或计数失维护 |
| `workflow_revisions.workflow_key`（:225） | **unique 第 2 列** `(workspace_id, workflow_key, version)`(:232) + `idx_workflow_revisions_active`(:661) | revision 解析（全链最热的查询之一，`queries/workflow_revisions.py:150-161`） | **需 schema 迁移** | unique 约束重建；revision_id 组合串成分（类别 3） |
| `workflow_node_codes.workflow_key`（:833） | **unique 第 2 列**(:843) + partial unique(:846) | **迁移考古表**：运行时解析走 `versioned_entities`（`NodeCodeService`），本表仅由 schema replay 创建、供 v26 `migrate_versioned_entities` 搬运历史数据（`migrations/versioned_entities.py:65` 读它） | **保留**（旧表形状保持至 v26 重放策略改写，或 post-chain drop；fresh install 的迁移链依赖其形状，codex on #256） | 兼容 `versioned_entities.entity_key` 的 `key:node` 编码（类别 3） |
| `quality_sample_batches.workflow_key`（:891，default ''） | 无索引 | 抽样过滤谓词（`quality_sampling.py:101-103`） | **需 schema 迁移** | 可空语义（default ''），抽样可跨 workflow——退役后过滤维度只剩 workspace |

迁移链中的 workflow_key 数据迁移（不改现状，仅记录在案）：
`runs.py`/`runs_sql.py`（job_batches→runs 搬运，`runs.py:89` drop）、`versioned_entities.py:61,70`
（`workflow_key || ':' || node_key` 编码 entity_key）、`custom_node_codes.py`（建表
时期）、`node_cms_config.py`（按 `(workflow_key, node_key)` 改写 node_config）、
`external_connections*.py`（CMS 绑定搬运）、`workspace_execution_defaults.py`、
`code_executor.py`（旧 executor 绑定）、`workflow_catalog_retirement.py`（v50 退役
全局 catalog 时把 key 降级为纯文本）、`workspace_id_key_binding.py`（v62 本体）。

**DB 侧汇总**：运行时活跃 11 列（6 个 jobs 索引 + 3 张组合 PK 子表 + status-count
触发器全链 4 函数 + 3 触发器 + workflow_revisions 的 1 个 unique 约束）是「需
schema 迁移」；另有 3 张迁移考古表（workflow_node_codes / workspace_node_bindings /
job_batches）按类别 2 表格定性**保留**、不参与约束重建（动 node_codes 需改写 v26
重放策略）。没有一列可原地删除（列值即数据）。真正的「可原地替换」发生在**读法层**：所有
`select ... where workflow_key=%s` 谓词都可以先改为绑定 workspace_id（值恒等，
无需等列删除），列删除留到 Phase 4。

## 类别 3：组合字符串 id（构造点）

| 构造点 | 模式 | 处置建议 | 风险注记 |
|---|---|---|---|
| `server/app/jobs/queries/batch.py:36` | `run_id = f"{workspace_id}_{workflow_key}_{source_kind}_{payload_digest}"` | **需 schema 迁移 + 外部引用评估** | run_id 是对外持久 id；去掉 key 段会改变**新** run 的 id 形状（旧 id 无需重写——逐行自解析）；API/审计/质量抽样按 id 引用 |
| `server/app/jobs/queries/job_nodes.py:14-16`、`job_bulk.py:14-16` | `job_id = f"{workspace_id}_{workflow_key}_{safe_source_id}"`（两处 `_job_id` 重复实现） | **需 schema 迁移 + 外部引用评估** | job_id 是最外露的持久 id；v62 已证明「id 中含改名前缀」的行可自解析（`resolve_job_dir_candidates` 双路径读），但 id 形状变化影响一切外部记录（工单、导出、人工引用） |
| `server/app/services/workflow_revisions.py:51-52` | `revision_id = f"{workspace_id}:{definition.key}:v{version}"`（`definition.key` 即 workspace key） | **需 schema 迁移 + 外部引用评估** | revision_id 落 `jobs.workflow_revision_id` 冻结列、出现在 API 响应与 job 详情；快照行按 id 自解析，旧行不受影响 |
| `server/app/services/node_codes.py:42-52` | `entity_key = f"{workflow_key}:{node_key}"`（`versioned_entities.entity_key`，v26 迁移同构） | **需 schema 迁移（存量键迁移或双读）** | VersionedEntityStore 全部读写（`node_codes.py:131,145,160-162,178` 的 get_effective/get_by_version/list_versions）按精确键相等查询——**只改新行构造会让存量 workspace 行立即不可见并分叉历史**（codex on #256）：要么存量键一次性迁移，要么构造点双读两代键；`_ENTITY_KEY_SEPARATOR` 校验在 v62 后已是冗余防御 |
| `server/app/services/node_secrets.py:21-23` | vault 名 `f"node:{workflow_key}:{node_key}:{field}"` | **需 schema 迁移（vault 名重写）** | 存量 secret 名不改写即失联——退役时需要一次性 vault 键改名或双读；`node_cms_config.py:129` 迁移也生成过该形状 |
| `worker/code_runner.py:178`、`worker/execution/run.py:136` | manifest/claim 透传 `workflow_key` | 需契约协调（跨进程） | 与 AgentClaimResponse 同源 |
| `server/app/jobs/storage_layout.py:26-35` | 磁盘 `data/jobs/<workspace_id>/<shard>/<job_id>`（storage_dir 落列 `jobs.storage_dir`，目录含 job_id——job_id 又含 key 段） | 保留（v62 有意未重写存量） | **issue 明示的存量问题**：v62 改 workspace id 时旧行 storage_dir 指向旧前缀，靠 `resolve_job_dir_candidates` 双候选读兼容；Phase 3/4 需决定是否顺带迁移磁盘路径 |
| `server/app/services/job_artifact_objects.py:55-60` | S3 `jobs/{workspace_id}/{job_id}/{name}`、staging 前缀 | 保留（v62 有意未重写存量） | 同上：对象存储 key 前缀旧行未重写，读路径按行内 storage_key 直读 |
| `server/app/services/materials.py:130` | 材料 S3 `storage_key = f"{workspace_id}/{hash或id}/{filename}"` | 保留 | 材料前缀同样未随 v62 重写存量 |
| `server/app/workflows/workflow_manifest.py:8` | manifest `key` 字段读 job 行 | 需契约协调 | manifest 下发 Worker，属跨进程协议 |

## 类别 4：前端类型与字段（frontend/src，非测试）

生成物 `frontend/src/generated/api.ts`（54 处 snake + 3 处 camel）是 OpenAPI 再
生产物，**标注即可、不手改**：schema 覆盖 `WorkspaceStatsResponse`、
`JobSummaryResponse`、`RunRecord`、`WorkspaceRecord.default_workflow_key`、
`AgentClaimResponse`、`FailedNodeRunItem`、`JobBatchRequest`、`NodeLimitRequest`、
`QualitySampleBatch(CreateRequest)`、`WorkspaceAgentRouteEntry`、
`WorkflowRevisionSummary(Item)`、`StudioContextWorkflow`、
`StudioAgentActiveWorkflowResponse`、`WorkspaceSettingsPayload.workflowKey` 及 7 条
`workflows/{workflow_key}` 路径模板（api.ts 实测 7）。

手写消费点（63 处 camel + 77 处 snake，**28 个手写文件**（含 workspaceEventRefresh.ts 的 1 处注释性提及）——camel+snake 并集实测；类别 4 统计行的 24 是 camel-only 口径）。**读写链完整提示**（codex
on #256 补遗）：`stores/setting/actions/executionConfigActions.ts:6-15` 按
`workflow_key` 过滤并构造 `node_limits` 的 PUT 载荷、`pages/SettingsPage.tsx:50-53`
用该字段识别 Agent 路由节点——Phase 2/3 下线字段时这两处必须同批改造，否则
节点限制保存会继续发送已退役字段、设置页把 Agent 节点误判为 code 节点：

| 位置 | 用途 | 处置建议 | 风险注记 |
|---|---|---|---|
| `frontend/src/types/index.ts:74-77` `WorkspaceSettings`（派生 `WorkspaceSettingsPayload`） | settings 快照 blob 的 required `workflowKey` | 需契约协调（Phase 2 首批） | 派生类型——后端字段改 optional 后此处自动跟随 |
| `frontend/src/stores/setting/state.ts:34,53` + `actions/saveActions.ts:40` + `index.ts:48-49` | settingStore draft/快照往返、dirty 判定 | 需契约协调 | 全量 PUT 回传白名单里仍带 workflowKey（`extra=forbid` 契约） |
| `frontend/src/pages/WorkspaceMainPage.tsx:60,115,121` | `workspaceStats?.workflow_key` 驱动 onboarding/空态 | 需契约协调（改读 workspace_id） | `onboardingReadiness.ts:32` 的 `!== undefined` settle 判定依赖字段存在性 |
| `frontend/src/lib/onboardingReadiness.ts:13,38,96,104` | 空态引导 + agent 路由过滤（`route.workflow_key === workflowKey`） | 需契约协调 | 过滤键改 workspace_id 语义不变 |
| `frontend/src/hooks/useWorkspaceRerunActions.ts:11-13` | 失败重跑上下文传 key | 需契约协调 | 同 failureApi 链 |
| `frontend/src/api/failureApi.ts:10,13` + `lib/queryKeysExtra.ts:35-36` | `workflow_key` 查询参数 + react-query 缓存键 | 需契约协调 | 缓存键含 key——字段下线时缓存键要同步换 workspace_id，避免灰度期串缓存 |
| `frontend/src/components/AddItemsDialog.tsx:81,134,157,171,193,268` | intake 提交 + 未发布守卫（`!workflowKey` 分支） | 需契约协调 | v62 后 key 恒非空，`!workflowKey` 空分支不可达（防御残留） |
| `frontend/src/components/LocalNodeLimitSection.tsx:11,16,47,69` | 节点限制过滤 | 需契约协调 | 过滤键 |
| `frontend/src/features/workflowStudio/**`（code-editor/inspector/validation/shared 9 文件） | `/workflows/{workflowKey}/` URL 拼接与展示 | 需契约协调（与 URL 路径参数同批） | `WorkflowNodeCodeSection.tsx:19` 硬拼 API 路径 |
| `frontend/src/pages/DashboardPage.tsx:20` | `default_workflow_key` 作 label 兜底 | 需契约协调（最后批） | 应改 workflow_label |
| `frontend/src/lib/workflowNodes.ts:16-19,48`、`pages/jobDetail/deriveJobDetailPresentation.ts:8-9,27` | job↔定义匹配、详情 label | 需契约协调 | 匹配键改 workspace 维度 |
| `frontend/src/hooks/useWorkspaceOnboardingSteps.ts:16,30,39`、`useWorkspaceSettingsQuery.ts:95`、`SettingsPage.tsx:148`（注释）、`canvas/workflowStudioEmptyState.ts:35` | 引导参数、快照、空态草稿 key | 需契约协调 | `SettingsPage` 已有「key 不可变」UI 注释，随字段删除一并清理 |

## 汇总统计

口径：非测试代码；生成物（api.ts / openapi.json）单独标注不重复计入。

| 维度 | 数量 |
|---|---|
| 后端 `workflow_key` 词匹配（119 文件全量；106 为排除 db/ 迁移考古 13 文件后的口径） | 598 处（其中 migrations+schema 98、routes 契约 20） |
| 后端 `default_workflow_key` | 59 处（20 文件） |
| 前端 camel `workflowKey`（24 文件，含 api.ts 3） | 63 处 |
| 前端 snake `workflow_key`（含 api.ts 54） | 77 处（排除规则 `!*test*`——同时排除 *.test.* 与 testing/fixtures 等测试辅助文件；用 `!*.test.*` 则为 80） |
| 类别 1 API 契约字段（Pydantic） | 响应 14 + 请求 6 = 20 个字段，另有 11 条 URL 路径/查询参数（node-code 路由 7 + MCP 工具 2 + 查询参数 2） |
| 类别 2 DB 列 | 运行时活跃 11 列（含 PK/unique/索引成员 8 列）+ 考古表 3 列（workflow_node_codes / workspace_node_bindings / job_batches，不参与约束重建——codex on #256）；6 个 jobs 索引、**4 个触发器函数 + 3 个触发器**、workflow_revisions 1 个 unique 约束 |
| 类别 3 组合字符串 id | 5 个构造族（run_id / job_id ×2 实现 / revision_id / entity_key / vault secret 名）+ 3 类存储路径（磁盘 job dir / S3 artifact / S3 material） |
| 类别 4 前端消费点 | 28 个手写文件（camel+snake 并集）+ 1 个生成物 |

处置分布（按引用点粗分）：

- **可原地替换**（改读法/改谓词，值恒等、无数据迁移）：约 70% ——包括全部
  service/queries 层的 `where workflow_key=%s` 谓词、worker 扫描、路由过滤键、
  前端过滤/匹配键。这些可先行绑定 workspace_id 读法，不等列删除。
- **需 schema 迁移**（列值重写/删列/索引与 PK 重建/触发器重写）：类别 2 的
  运行时活跃 11 列（+ 配套索引/约束/触发器全链）+ 类别 3 的 5 个组合 id 构造点
  + vault 名——约占 20%，且是全部的重活。
- **需契约协调**（API 字段下线需前端/Worker/MCP 同步）：类别 1 全部 26 个
  契约触点 + 类别 4 前端消费链 ——约占 10%，但决定退役节奏（必须先于 DB）。
- **保留**：迁移考古表（workflow_node_codes、workspace_node_bindings、
  job_batches）、磁盘/S3 存量路径（v62 已有意不重写，读路径按行自解析）、
  审计性质的外部 id 引用。

## Phase 2-4 建议切分

**Phase 2（契约先行，纯 API + 前端，无 DB 变更）**

1. 先 deprecated 的一批（前端只读）：`WorkspaceStatsResponse.workflow_key`、
   `JobSummaryResponse.workflow_key`、`RunRecord.workflow_key`、
   `WorkflowRevisionSummary.workflow_key`、`FailedNodeRunItem.workflow_key`、
   `WorkspaceAgentRouteEntry.workflow_key` ——OpenAPI description 标 deprecated，
   前端全部改读 `workspace_id`（值恒等，行为零变化），观察一个发布周期后删字段。
2. settings blob：`WorkspaceSettingsPayload.workflowKey` 从 required 降 optional
   → 前端 settingStore/快照/dirty 判定去字段 → 后端 PUT 白名单收窄。注意
   `saveActions.ts` 全量回传是 `extra=forbid`，两侧必须同 PR 批次协调。
3. 请求参数（`JobBatchRequest`、`RunCreateRequest`、失败过滤、
   `QualitySampleBatchCreateRequest`）：先容忍「缺省 = 取 workspace_id」的
   服务端默认，再 deprecated，最后删。失败过滤链（failureApi → queryKeys →
   useFailureCategories）注意缓存键同步换。
4. URL 路径参数 `{workflow_key}` 与 MCP 工具面：**最后**处理——外部引用兼容性
   最高，建议长期保留（路径里两段恒等不伤语义），或至少给一个完整弃用窗口。

**Phase 3（DB 列与组合 id，逐 PR）**

- 迁移最重的是 `jobs.workflow_key`（6 个索引 + 扫描谓词 + dedup 键 +
  **status-count 触发器全链：4 个函数 + 3 个触发器**，见类别 2 表格）。建议
  顺序：先把全部查询谓词换绑 workspace_id（可原地替换部分，独立 PR 可验证）→
  再做「删列 + 索引重建」的纯 DDL 迁移——**删列前必须逐一处理全部七个对象**
  （`jobs_node_status_counts_rekey` 直接依赖 `UPDATE OF workflow_key`，漏掉
  即 DDL 失败；deduct 链漏掉则计数失维护）。
- 组合 PK 子表三张（node_limits / node_routes / node_capacities）适合一个
  PR：同构的 PK 重建（去掉 key 维度），`workspace_node_capacities` 顺带评估
  整体退役（legacy 投影）。
- `workflow_revisions` 的 unique 约束重建与 `versioned_entities.entity_key`
  编码简化可合并评估（`key:node` 家族）——**但 entity_key 的编码简化有存量键
  硬约束**（类别 3 表格）：VersionedEntityStore 全部读写按精确键相等查询，只改
  新行构造会让存量 workspace 行立即不可见并分叉历史——必须存量键一次性迁移或
  构造点双读两代键，**全局行与 workspace 行同此约束**（全局行额外没有可替代的
  workspace id，直接去 key 段还会使同名节点冲突、历史 replay pin 失解析）。
  **`workflow_node_codes` 除外**——它是迁移考古表（类别 2 表格已定性「保留」），
  不参与约束重建；若 Phase 3 想动它，必须同步改写 v26 的 fresh-install 历史重放
  策略，否则迁移链断裂（codex on #256）。
- 组合字符串 id（run_id/job_id/revision_id）：**不建议重写存量**——沿用 v62
  的「旧行自解析」先例，只改新行构造（去 key 段）；外部引用（工单/导出/审计）
  按 id 前缀自解析。vault secret 名是唯一必须显式处理存量的（改名或双读），
  单独 PR。**例外：run_id 是幂等键**（`queries` 的 `create_run` 以确定性 run_id
  作重复提交幂等键；异步 intake 在 job 去重前先 upsert run）——发布前已提交的
  相同异步 payload，发布后会算出不同 ID、无法命中旧 run 的 upsert/requeue 分支，
  留下重复 run/审计记录。切版点需要旧 ID 探测或兼容映射，不能只靠旧行自解析
  （codex on #256）。
- 存量磁盘/S3 路径：与 v62 决策保持一致——不强制迁移，读路径已双候选兼容；
  若 Phase 4 想清理，做成可选的运维脚本而非迁移。

**Phase 4（收尾）**

- 删 `workspaces.default_workflow_key` 列（终态：workspaces 只有 id）；
  `jobs/queries/workspace.py` 的 create/update 签名去 `default_workflow_key`
  参数；`workspace_stats.py` 空分支与 `AddItemsDialog` 的 `!workflowKey` 分支
  （v62 后不可达的防御残留）一并清理。**`StudioAgentActiveWorkflowResponse` 的
  empty 分支除外**——它由「无已发布 revision」驱动而非 key 可空（类别 1 表格），
  from-scratch 流程依赖，只能收紧 `workflow_key` 可空性、不得删该状态。
- 更新 invariant `DB-WORKSPACE-KEY-BINDING-001` statement 为终态描述
  （config/architecture/architecture-invariants.yaml），同步 AGENTS.md §6
  workflow 边界条目、`postgres_schema.sql` workspaces 表注释、
  `docs/architecture/backend.md` Database 节。

## 复核命令

```bash
rg -n "workflow_key" server/app --glob '!*test*' | wc -l          # 598
rg -n "default_workflow_key" server/app | wc -l                   # 59
rg -n "workflowKey" frontend/src --glob '!*.test.*' | wc -l       # 63
rg -n "workflow_key" frontend/src --glob '!*.test.*' | wc -l      # 77
rg -n "workflow_key" server/app/db/postgres_schema.sql | wc -l    # 52
```
