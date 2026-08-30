# 后端架构

## Overview

Agent Legion 后端基于 FastAPI，提供 REST API、SSE 事件推送和 WebSocket Agent 状态。核心职责包括：

- Agent Legion DAG 工作流执行（Workspace / Job / Node）
- 实例级外部服务连接（endpoint/凭据注入节点 dispatch）
- PostgreSQL 持久化与本地文件系统管理

## Directory Structure

```
server/app/
├── main.py                 # FastAPI 应用工厂 + 生命周期
├── routes/                 # REST API 路由
│   ├── agent_workers.py    # Agent Worker 注册、心跳、任务领取
│   ├── agents.py           # Agent 状态查询 (WebSocket)
│   ├── artifacts.py        # Agent Worker artifact 上传/下载
│   ├── common.py           # 健康检查等公共端点
│   ├── dashboard_events.py # Dashboard SSE 事件推送
│   ├── job_*.py            # Job 相关路由与合约
│   ├── jobs.py             # Agent Legion Job API
│   ├── packages.py         # 打包管理
│   ├── skill_catalog_route.py # Skill 目录查询
│   ├── token_usage.py      # Token 用量统计
│   ├── worker.py           # Worker 控制（暂停/恢复）
│   ├── workflow_*.py       # 工作流修订、草稿对比与节点代码发布
│   ├── workspace_*.py      # Workspace、执行器、设置
│   └── __init__.py         # 路由组装
├── services/               # 业务逻辑服务层
│   ├── job_*.py            # Job 查询、执行、重跑、删除、打包
│   ├── token_usage*.py     # Token 用量统计与定价
│   ├── workflow_*.py       # 工作流草稿、修订、格式转换
│   ├── workspace_*.py      # Workspace 配置与执行器配置
│   └── ...
├── workflows/              # Agent Legion DAG 定义与执行
│   ├── definition.py       # 工作流定义解析
│   ├── scheduler.py        # DAG 调度
│   ├── workflow_node_execution.py # 节点执行
│   ├── skills.py           # Skill 路径解析 / 契约检查
│   └── ...
├── db/                     # 数据库层
│   └── schema.py           # 表结构定义
├── jobs/                   # Job 领域查询与类型
│   └── queries/            # JobQueries（含 WorkspaceQueriesMixin）等
├── agent_control/          # Agent Worker 控制面（issue #191 归包）：registry.py
│                         # 注册/鉴权/liveness、register_tokens*.py scoped token
│                         # 生命周期、completion.py 执行结果提交、
│                         # declarations.py 声明归一化
├── agent_catalog/          # Agent 定义目录：definition.py AgentDefinition 模型、
│                         # builtin.py demo workflow 内置模板
├── configuration/          # 配置加载与 owned-keys 校验；executor_runtime.py
│                         # executor_runtime 配置模型（issue #188 中立化）
├── executors/              # Code executor、租赁调度与 capacity 控制
├── events/                 # 事件总线、Agent 发现与状态跟踪（agents.py）、WS 广播
├── workflow_worker/      # DAG workflow worker：thread.py 线程、ready.py 每 pass
│                         # 一次的 ready 候选收集、schedule.py lease 认领与提交、
│                         # agent_stock.py 产能库存配置
```

平台只携带无业务色彩的通用协议实现：实例级外部服务连接内置
`static_bearer` 与 `hmac_token`（通用 HMAC 签名换 token）adapter
（`server/app/services/connection_adapters.py` / `connection_adapter_hmac.py`），
具体业务系统的专属鉴权协议由业务侧以自定义节点代码承载。

## Data Flow

```
客户端请求 → FastAPI Router → Service Layer → DB / Executors
                     ↓
         SSE Events / WebSocket ← DB Notifications
                     ↓
               前端实时更新
```

`WorkflowWorkerThread` 定期轮询数据库，驱动 Agent Legion DAG Job 从 `queued` 向 `completed` 状态推进；Agent 路由节点经 `AgentExecutionBroker` 派发到 Worker，其余路由节点进入本地隐含 code 池执行。

## Key Decisions

- PostgreSQL 是唯一运行时数据库，通过连接池支撑多进程、多设备并发协调。
- Agent Legion DAG 是唯一的执行模型；workflow 是 workspace 内部的一份 DAG（schema v50 退役全局 catalog）。schema v62 起 workspace id 即 workflow key（创建时显式提供、终身不可变），创建路径不再种子示例模板——示例 workflow（`education_video_problems_generation`）仅经 `make import-demo` 提供 demo workspace，业务 workflow 经 workspace revision 发布 + 自定义节点发布承载。
- 所有文件 I/O 限制在 `data/` 目录内，由 `security.py` 做路径校验。
- 路由、服务、执行器之间有明确的边界：Route 只做 HTTP 适配，Service 处理业务逻辑，Executor 通过租赁（lease）申请容量。详见 [AGENTS.md](../../AGENTS.md)。
- 外部服务凭据与端点配置走实例级外部服务连接（admin 全局设置「外部服务连接」），见下文 Configuration Reference。
- CORS 来源由 env `AGENT_LEGION_CORS_ALLOW_ORIGINS` / `AGENT_LEGION_CORS_ALLOW_CREDENTIALS` 显式配置；默认仅允许本机 Vite 开发源。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### REST API 路由

> 所有路由挂载在 `/api` 前缀下。

| 方法 | 路径 | 处理函数 | 文件 |
|------|------|----------|------|
| GET | `/agent-definitions` | `list_agent_definitions` | routes/agent_definitions.py |
| POST | `/agent-definitions` | `create_agent_definition` | routes/agent_definitions.py |
| GET | `/agent-definitions/{agent_id}` | `get_agent_definition` | routes/agent_definitions.py |
| GET | `/agent-definitions/{agent_id}/versions` | `list_agent_definition_versions` | routes/agent_definitions.py |
| PUT | `/agent-definitions/{agent_id}/draft` | `save_agent_definition_draft` | routes/agent_definitions.py |
| POST | `/agent-definitions/{agent_id}/publish` | `publish_agent_definition` | routes/agent_definitions.py |
| POST | `/agent-definitions/{agent_id}/rollback` | `rollback_agent_definition` | routes/agent_definitions.py |
| POST | `/agent-definitions/{agent_id}/copy` | `copy_agent_definition` | routes/agent_definitions.py |
| DELETE | `/agent-definitions/{agent_id}` | `archive_agent_definition` | routes/agent_definitions.py |
| POST | `/agent-register-tokens` | `create_register_token` | routes/agent_register_tokens.py |
| GET | `/agent-register-tokens` | `list_register_tokens` | routes/agent_register_tokens.py |
| DELETE | `/agent-register-tokens/{token_id}` | `delete_register_token` | routes/agent_register_tokens.py |
| POST | `/agent-executions/claim` | `claim` | routes/agent_worker_claims.py |
| POST | `/agent-executions/{execution_id}/heartbeat` | `heartbeat` | routes/agent_worker_claims.py |
| GET | `/agent-workers/self/metrics` | `get_worker_metrics` | routes/agent_worker_metrics.py |
| POST | `/agent-workers/register` | `register` | routes/agent_workers.py |
| GET | `/agent-workers/self` | `get_worker_self` | routes/agent_workers.py |
| DELETE | `/agent-workers/{worker_id}` | `delete_worker` | routes/agent_workers.py |
| GET | `/agent-workers` | `list_workers` | routes/agent_workers.py |
| GET | `/agent-executions/{execution_id}/bundle` | `bundle` | routes/agent_workers.py |
| POST | `/agent-executions/{execution_id}/release-slot` | `release_slot` | routes/agent_workers.py |
| POST | `/agent-executions/{execution_id}/result` | `result` | routes/agent_workers.py |
| GET | `/agents` | `list_agents` | routes/agents.py |
| WEBSOCKET | `/agents` | `agents_ws` | routes/agents.py |
| POST | `/artifacts` | `upload_artifact` | routes/artifacts.py |
| GET | `/artifacts/{hash}` | `download_artifact` | routes/artifacts.py |
| GET | `/health` | `health` | routes/common.py |
| GET | `/admin/connections` | `list_connections` | routes/connections.py |
| GET | `/admin/connection-types` | `list_connection_types` | routes/connections.py |
| POST | `/admin/connections` | `create_connection` | routes/connections.py |
| PUT | `/admin/connections/{key}` | `update_connection` | routes/connections.py |
| DELETE | `/admin/connections/{key}` | `delete_connection` | routes/connections.py |
| POST | `/admin/connections/{key}/test` | `test_connection` | routes/connections.py |
| GET | `/dashboard/events` | `dashboard_events` | routes/dashboard_events.py |
| GET | `/workspaces/{workspace_id}/failed-node-runs` | `list_failed_node_runs` | routes/failed_node_runs.py |
| POST | `/workspaces/{workspace_id}/jobs/rerun-by-failure` | `rerun_jobs_by_failure_category` | routes/failed_node_runs.py |
| GET | `/admin/instance-settings` | `get_instance_settings` | routes/instance_settings.py |
| PUT | `/admin/instance-settings` | `put_instance_settings` | routes/instance_settings.py |
| GET | `/jobs/{job_id}/artifacts/{artifact_name}/raw` | `get_artifact_raw` | routes/job_artifact_raw.py |
| GET | `/jobs/{job_id}/artifacts/{artifact_name:path}` | `get_artifact` | routes/job_artifacts.py |
| GET | `/jobs/{job_id}/runs/{run_id}/log` | `get_job_run_log` | routes/job_artifacts.py |
| POST | `/workspaces/{workspace_id}/job-batches` | `create_workspace_job_batch` | routes/job_batches.py |
| GET | `/jobs/{job_id}/{invalid_path:path}` | `reject_invalid_job_subpath` | routes/job_invalid_paths.py |
| GET | `/workspaces/{workspace_id}/jobs/snapshot` | `snapshot_workspace_jobs` | routes/job_list.py |
| GET | `/workspaces/{workspace_id}/jobs/facets` | `workspace_job_facets` | routes/job_list.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-rerun` | `batch_rerun_workspace_jobs` | routes/job_mutations.py |
| DELETE | `/workspaces/{workspace_id}/jobs/batch` | `batch_delete_workspace_jobs` | routes/job_mutations.py |
| POST | `/jobs/{job_id}/nodes/{node_key}/rerun` | `rerun_node` | routes/job_mutations.py |
| DELETE | `/jobs/{job_id}` | `delete_job` | routes/job_mutations.py |
| POST | `/jobs/{job_id}/run-to` | `run_to` | routes/job_mutations.py |
| POST | `/jobs/{job_id}/continue` | `continue_job` | routes/job_mutations.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-run-to` | `batch_run_to` | routes/job_mutations.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-pause` | `batch_pause_workspace_jobs` | routes/job_pause_batch.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-resume` | `batch_resume_workspace_jobs` | routes/job_pause_batch.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-rerun/preview` | `preview_batch_rerun_workspace_jobs` | routes/job_rerun_preview.py |
| POST | `/workspaces/{workspace_id}/events/stress` | `record_stress_events` | routes/job_stress_events.py |
| POST | `/jobs/{job_id}/upgrade-workflow` | `upgrade_job_workflow` | routes/job_workflow_upgrade.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-upgrade-workflow` | `batch_upgrade_jobs_workflow` | routes/job_workflow_upgrade_batch.py |
| GET | `/workspaces/{workspace_id}/jobs` | `list_workspace_jobs` | routes/jobs.py |
| GET | `/jobs/{job_id}` | `get_job` | routes/jobs.py |
| POST | `/workspaces/{workspace_id}/material-bundles` | `create_bundle` | routes/material_bundles.py |
| GET | `/workspaces/{workspace_id}/material-bundles` | `list_bundles` | routes/material_bundles.py |
| GET | `/workspaces/{workspace_id}/material-bundles/{bundle_id}` | `get_bundle` | routes/material_bundles.py |
| DELETE | `/workspaces/{workspace_id}/material-bundles/{bundle_id}` | `delete_bundle` | routes/material_bundles.py |
| POST | `/workspaces/{workspace_id}/materials/presign` | `presign_material` | routes/materials.py |
| POST | `/workspaces/{workspace_id}/materials/{material_id}/complete` | `complete_material` | routes/materials.py |
| GET | `/workspaces/{workspace_id}/materials` | `list_materials` | routes/materials.py |
| GET | `/workspaces/{workspace_id}/materials/{material_id}` | `get_material` | routes/materials.py |
| DELETE | `/workspaces/{workspace_id}/materials/{material_id}` | `delete_material` | routes/materials.py |
| GET | `/metrics/overview` | `get_metrics_overview` | routes/metrics.py |
| POST | `/workspaces/{workspace_id}/jobs/clear-packed` | `clear_workspace_jobs_packed_status` | routes/package_clear_packed.py |
| GET | `/workspaces/{workspace_id}/packages` | `list_workspace_packages` | routes/packages.py |
| DELETE | `/workspaces/{workspace_id}/packages/{package_id:int}` | `delete_workspace_package_route` | routes/packages.py |
| PATCH | `/workspaces/{workspace_id}/packages/{package_id:int}` | `update_workspace_package_route` | routes/packages.py |
| POST | `/workspaces/{workspace_id}/jobs/package` | `package_workspace_jobs` | routes/packages.py |
| GET | `/workspaces/{workspace_id}/packages/{filename:path}` | `download_workspace_package` | routes/packages.py |
| POST | `/workspaces/{workspace_id}/quality/sample-batches` | `create_sample_batch` | routes/quality.py |
| GET | `/workspaces/{workspace_id}/quality/sample-batches` | `list_sample_batches` | routes/quality.py |
| GET | `/workspaces/{workspace_id}/quality/sample-batches/{batch_id}` | `get_sample_batch` | routes/quality.py |
| GET | `/workspaces/{workspace_id}/quality/sample-batches/{batch_id}/stats` | `get_sample_batch_stats` | routes/quality.py |
| GET | `/workspaces/{workspace_id}/quality/sample-items/{item_id}` | `get_sample_item` | routes/quality.py |
| POST | `/workspaces/{workspace_id}/quality/sample-items/{item_id}/labels` | `add_sample_item_label` | routes/quality.py |
| POST | `/workspaces/{workspace_id}/quality/sample-items/{item_id}/replays` | `create_replay` | routes/quality_replays.py |
| GET | `/workspaces/{workspace_id}/quality/sample-items/{item_id}/replays` | `list_replays` | routes/quality_replays.py |
| GET | `/workspaces/{workspace_id}/quality/replays/{replay_id}` | `get_replay` | routes/quality_replays.py |
| POST | `/workspaces/{workspace_id}/runs` | `create_run` | routes/runs.py |
| GET | `/workspaces/{workspace_id}/runs` | `list_runs` | routes/runs.py |
| GET | `/workspaces/{workspace_id}/runs/{run_id}` | `get_run` | routes/runs.py |
| GET | `/agent-catalog/skills/{skill_key:path}` | `get_skill` | routes/skill_catalog_route.py |
| GET | `/admin/skill-sources` | `get_skill_sources` | routes/skill_sources.py |
| PUT | `/admin/skill-sources/{skill_key:path}` | `put_skill_source` | routes/skill_sources.py |
| POST | `/admin/skill-sources/relock` | `relock_skill_sources` | routes/skill_sources.py |
| POST | `/skills/validate` | `validate_skill` | routes/skills.py |
| GET | `/skills/tags` | `list_skill_tags` | routes/skills.py |
| GET | `/studio-agent/tools/chat-sessions/{session_id}/context` | `get_chat_session_context` | routes/studio_agent_context.py |
| GET | `/studio-agent/tools/skills/{skill_key:path}` | `get_skill` | routes/studio_agent_skill_tools.py |
| POST | `/studio-agent/tools/skills/{skill_key:path}/validate` | `validate_skill` | routes/studio_agent_skill_tools.py |
| POST | `/studio-agent/tools/skills/{skill_key:path}/versions` | `save_skill_version` | routes/studio_agent_skill_tools.py |
| POST | `/studio-agent-tokens` | `mint_token` | routes/studio_agent_tokens.py |
| GET | `/studio-agent-tokens` | `list_tokens` | routes/studio_agent_tokens.py |
| DELETE | `/studio-agent-tokens/{token_id}` | `revoke_token` | routes/studio_agent_tokens.py |
| POST | `/studio-agent/tools/workspaces/{workspace_id}/workflow/validate` | `validate_workflow` | routes/studio_agent_tools.py |
| POST | `/studio-agent/tools/workspaces/{workspace_id}/workflow/compare` | `compare_workflow` | routes/studio_agent_tools.py |
| PUT | `/studio-agent/tools/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/draft` | `save_node_code_draft` | routes/studio_agent_tools.py |
| PUT | `/studio-agent/tools/workspaces/{workspace_id}/agent-definitions/{agent_id}/draft` | `save_agent_definition_draft` | routes/studio_agent_tools.py |
| GET | `/studio-agent/tools/workspaces/{workspace_id}/workflow/active` | `get_active_revision` | routes/studio_agent_tools.py |
| GET | `/studio-agent/tools/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code` | `get_node_code_state` | routes/studio_agent_tools.py |
| GET | `/admin/studio-agents` | `get_studio_agents` | routes/studio_agents_admin.py |
| PUT | `/admin/studio-agents` | `put_studio_agents` | routes/studio_agents_admin.py |
| GET | `/workspaces/{workspace_id}/studio-chat/agents` | `list_agents` | routes/studio_chat.py |
| POST | `/workspaces/{workspace_id}/studio-chat/sessions` | `create_session` | routes/studio_chat.py |
| GET | `/workspaces/{workspace_id}/studio-chat/sessions` | `list_sessions` | routes/studio_chat.py |
| GET | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}` | `get_session` | routes/studio_chat.py |
| DELETE | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}` | `close_session` | routes/studio_chat.py |
| POST | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/resume` | `resume_session` | routes/studio_chat.py |
| GET | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/messages` | `list_messages` | routes/studio_chat.py |
| POST | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/messages` | `send_message` | routes/studio_chat.py |
| POST | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/cancel` | `cancel_turn` | routes/studio_chat.py |
| POST | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/permissions/allow-all` | `set_allow_all` | routes/studio_chat.py |
| POST | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/permissions/{request_id}` | `answer_permission` | routes/studio_chat.py |
| PUT | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/context` | `update_context` | routes/studio_chat_context.py |
| GET | `/workspaces/{workspace_id}/studio-chat/sessions/{session_id}/events` | `session_events` | routes/studio_chat_events.py |
| GET | `/jobs/{job_id}/runs/{run_id}/token-usage` | `get_run_token_usage` | routes/token_usage.py |
| GET | `/jobs/{job_id}/token-usage` | `get_job_token_usage` | routes/token_usage.py |
| GET | `/workspaces/{workspace_id}/token-usage` | `get_workspace_token_usage` | routes/token_usage.py |
| GET | `/admin/token-usage-pricing` | `get_token_usage_pricing` | routes/token_usage_pricing.py |
| PUT | `/admin/token-usage-pricing` | `put_token_usage_pricing` | routes/token_usage_pricing.py |
| GET | `/worker/status` | `worker_status` | routes/worker.py |
| POST | `/worker/pause` | `pause_worker` | routes/worker.py |
| POST | `/worker/resume` | `resume_worker` | routes/worker.py |
| POST | `/workspaces/{workspace_id}/workflow-drafts/compare` | `compare_workflow_draft_route` | routes/workflow_draft_compare.py |
| POST | `/workspaces/{workspace_id}/workflow-drafts/validate` | `validate_workflow_draft` | routes/workflow_draft_publish.py |
| POST | `/workspaces/{workspace_id}/workflow-drafts/publish` | `publish_draft` | routes/workflow_draft_publish.py |
| GET | `/workspaces/{workspace_id}/workflow-draft` | `get_draft` | routes/workflow_draft_store.py |
| PUT | `/workspaces/{workspace_id}/workflow-draft` | `put_draft` | routes/workflow_draft_store.py |
| GET | `/workflow-node-code-template` | `get_node_code_template` | routes/workflow_node_codes.py |
| GET | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code` | `get_node_code` | routes/workflow_node_codes.py |
| PUT | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code` | `save_node_code_draft` | routes/workflow_node_codes.py |
| POST | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/publish` | `publish_node_code` | routes/workflow_node_codes.py |
| GET | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/versions` | `list_node_code_versions` | routes/workflow_node_codes.py |
| GET | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/versions/{version}` | `get_node_code_version` | routes/workflow_node_codes.py |
| POST | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/rollback` | `rollback_node_code` | routes/workflow_node_codes.py |
| DELETE | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code` | `archive_node_code` | routes/workflow_node_codes.py |
| GET | `/workspaces/{workspace_id}/workflow-revisions` | `list_workflow_revisions` | routes/workflow_revisions.py |
| GET | `/workspaces/{workspace_id}/workflow-revisions/active` | `get_active_workflow_revision` | routes/workflow_revisions.py |
| GET | `/workspaces/{workspace_id}/workflow-revisions/{revision_id}` | `get_workflow_revision_detail` | routes/workflow_revisions.py |
| GET | `/agent-catalog` | `get_agent_catalog` | routes/workspace_agent_catalog.py |
| GET | `/workspaces/{workspace_id}/execution-configuration` | `get_workspace_execution_configuration` | routes/workspace_agent_catalog.py |
| GET | `/workspaces/{workspace_id}/agent-routes` | `get_workspace_agent_routes` | routes/workspace_agent_routes.py |
| PUT | `/workspaces/{workspace_id}/configuration` | `replace_workspace_configuration` | routes/workspace_configuration.py |
| GET | `/workspaces/{workspace_id}/node-runs` | `list_workspace_runs` | routes/workspace_runs.py |
| GET | `/workspaces/{workspace_id}/dag` | `get_workspace_dag` | routes/workspace_runs.py |
| GET | `/workspaces/{workspace_id}/runtime-models` | `get_workspace_runtime_models` | routes/workspace_runtime_models.py |
| GET | `/workspaces/{workspace_id}/secrets` | `list_workspace_secrets` | routes/workspace_secrets.py |
| PUT | `/workspaces/{workspace_id}/secrets/{name}` | `put_workspace_secret` | routes/workspace_secrets.py |
| DELETE | `/workspaces/{workspace_id}/secrets/{name}` | `delete_workspace_secret` | routes/workspace_secrets.py |
| GET | `/workspaces/{workspace_id}/settings` | `get_workspace_settings` | routes/workspace_settings.py |
| PATCH | `/workspaces/{workspace_id}/settings/{section}` | `update_workspace_settings_section` | routes/workspace_settings.py |
| GET | `/workspaces` | `list_workspaces` | routes/workspaces.py |
| POST | `/workspaces` | `create_workspace` | routes/workspaces.py |
| GET | `/workspaces/{workspace_id}` | `get_workspace` | routes/workspaces.py |
| PATCH | `/workspaces/{workspace_id}` | `update_workspace` | routes/workspaces.py |
| DELETE | `/workspaces/{workspace_id}` | `delete_workspace` | routes/workspaces.py |
| GET | `/workspaces/{workspace_id}/stats` | `get_workspace_stats` | routes/workspaces.py |
| GET | `/workspaces/{workspace_id}/events` | `workspace_events` | routes/workspaces.py |

### 数据模型

| 模型 | 类型 | 字段 | 文件 |
|------|------|------|------|
| AgentDefinition | BaseModel | capability: str, runtime: Literal['pi', 'openclaw', 'velites'], skill: str, t... | app/agent_catalog/definition.py |
| AgentEnqueueConfig | BaseModel | workers: int, max_pending: int | app/configuration/executor_knobs.py |
| AgentStockConfig | BaseModel | enabled: bool, window_seconds: int, horizon_seconds: int, min_stock: int, max... | app/configuration/executor_knobs.py |
| CodeStockConfig | BaseModel | enabled: bool, factor: float, min_stock: int, max_stock: int, refresh_seconds... | app/configuration/executor_knobs.py |
| OpenClawRuntimeConfig | BaseModel | cwd: str | app/configuration/executor_runtime.py |
| WorkflowsRuntimeConfig | BaseModel | enabled: bool, custom_nodes_enabled: bool | app/configuration/executor_runtime.py |
| AgentWorkersRuntimeConfig | BaseModel | max_archive_bytes: int, min_protocol_version: int | app/configuration/executor_runtime.py |
| ExecutorRuntimeConfig | BaseModel | heartbeat_interval_seconds: float, lease_ttl_seconds: int, heartbeat_failure_... | app/configuration/executor_runtime.py |
| CodeCapabilityConfig | BaseModel | timeout_seconds: int, sandbox_network: bool, config_schema: dict[str, Any] | app/executors/contracts.py |
| AgentDefinitionResponse | BaseModel | id: str, runtime: Literal['pi', 'openclaw', 'velites'], capability: str, skil... | app/routes/agent_catalog_contracts.py |
| AgentCatalogResponse | BaseModel | agents: list[AgentDefinitionResponse] | app/routes/agent_catalog_contracts.py |
| AgentDefinitionPayload | BaseModel | capability: str, runtime: Literal['pi', 'openclaw', 'velites'], skill: str, t... | app/routes/agent_definition_contracts.py |
| AgentCopyRequest | BaseModel | new_agent_id: str | app/routes/agent_definition_contracts.py |
| AgentRollbackRequest | BaseModel | version: int | app/routes/agent_definition_contracts.py |
| AgentVersionResponse | BaseModel | id: str, agent_id: str, version: int, status: Literal['draft', 'published', '... | app/routes/agent_definition_contracts.py |
| AgentVersionSummary | BaseModel | id: str, agent_id: str, version: int, status: Literal['draft', 'published', '... | app/routes/agent_definition_contracts.py |
| AgentListItem | BaseModel | agent_id: str, capability: str, runtime: str, skill: str, version: int, statu... | app/routes/agent_definition_contracts.py |
| AgentListResponse | BaseModel | agents: list[AgentListItem] | app/routes/agent_definition_contracts.py |
| AgentDetailResponse | BaseModel | agent_id: str, latest: AgentVersionResponse | None, published: AgentVersionRe... | app/routes/agent_definition_contracts.py |
| AgentVersionsResponse | BaseModel | versions: list[AgentVersionSummary] | app/routes/agent_definition_contracts.py |
| AgentArchiveResponse | BaseModel | archived: int | app/routes/agent_definition_contracts.py |
| RegisterAgentWorkerRequest | BaseModel | worker_id: str, name: str, runtimes: list[str], capabilities: list[str], mode... | app/routes/agent_workers_contracts.py |
| AgentWorkerWorkspace | BaseModel | workspace_id: str, workspace_name: str, token_ids: list[str] | app/routes/agent_workers_contracts.py |
| RegisterAgentWorkerResponse | BaseModel | worker_token: str, host_protocol_version: int, allowed_workspaces: list[str],... | app/routes/agent_workers_contracts.py |
| CreateAgentRegisterTokenRequest | BaseModel | workspace_id: str, label: str | app/routes/agent_workers_contracts.py |
| AgentRegisterTokenCreatedResponse | BaseModel | token_id: str, register_token: str, workspace_id: str, label: str | app/routes/agent_workers_contracts.py |
| AgentRegisterTokenSummary | BaseModel | token_id: str, workspace_id: str | None, label: str, created_at: str, revoked... | app/routes/agent_workers_contracts.py |
| AgentRegisterTokensResponse | BaseModel | tokens: list[AgentRegisterTokenSummary] | app/routes/agent_workers_contracts.py |
| AgentRegisterTokenDeleteResponse | BaseModel | token_id: str, deleted: bool, cascaded_worker_ids: list[str] | app/routes/agent_workers_contracts.py |
| ClaimAgentExecutionRequest | BaseModel | worker_id: str, max_concurrency: int | None, max_code_concurrency: int | None | app/routes/agent_workers_contracts.py |
| AgentWorkerSummary | BaseModel | worker_id: str, name: str, runtimes: list[str], capabilities: list[str], mode... | app/routes/agent_workers_contracts.py |
| AgentWorkersResponse | BaseModel | workers: list[AgentWorkerSummary] | app/routes/agent_workers_contracts.py |
| AgentWorkerDeleteResponse | BaseModel | worker_id: str, deleted: bool | app/routes/agent_workers_contracts.py |
| AgentClaimResponse | BaseModel | execution_id: str, lease_id: str, workspace_id: str, job_id: str, workflow_ke... | app/routes/agent_workers_contracts.py |
| AgentHeartbeatResponse | BaseModel | cancelled_execution_ids: list[str] | app/routes/agent_workers_contracts.py |
| AgentStatusResponse | BaseModel | id: str, name: str, busy: bool | app/routes/agents.py |
| AgentsResponse | BaseModel | agents: list[AgentStatusResponse] | app/routes/agents.py |
| ArtifactUploadResponse | BaseModel | hash: str | app/routes/artifacts.py |
| LoginRequest | BaseModel | username: str, password: str | app/routes/auth_contracts.py |
| BootstrapRequest | BaseModel | username: str, password: str, display_name: str | app/routes/auth_contracts.py |
| UserResponse | BaseModel | id: str, username: str, display_name: str, role: Literal['admin', 'member'], ... | app/routes/auth_contracts.py |
| LoginResponse | BaseModel | user: UserResponse | app/routes/auth_contracts.py |
| MeResponse | BaseModel | user: UserResponse | app/routes/auth_contracts.py |
| BootstrapStatusResponse | BaseModel | available: bool | app/routes/auth_contracts.py |
| UserCreateRequest | BaseModel | username: str, password: str, display_name: str, role: Literal['admin', 'memb... | app/routes/auth_contracts.py |
| UserPatchRequest | BaseModel | display_name: str | None, role: Literal['admin', 'member'] | None, password: ... | app/routes/auth_contracts.py |
| UsersResponse | BaseModel | users: list[UserResponse] | app/routes/auth_contracts.py |
| MemberResponse | BaseModel | id: str, username: str, display_name: str, user_role: Literal['admin', 'membe... | app/routes/auth_contracts.py |
| MembersResponse | BaseModel | members: list[MemberResponse] | app/routes/auth_contracts.py |
| MemberPutRequest | BaseModel | user_id: str, role: Literal['editor', 'viewer'] | app/routes/auth_contracts.py |
| StorageStatus | BaseModel | configured: bool, reachable: bool | app/routes/common.py |
| HealthResponse | BaseModel | ok: bool, workers: dict[str, str] | None, storage: StorageStatus | None | app/routes/common.py |
| ConnectionCreate | BaseModel | key: str, type: str, display_name: str, config: dict[str, Any] | app/routes/connections_contracts.py |
| ConnectionUpdate | BaseModel | display_name: str | None, config: dict[str, Any] | None, enabled: bool | None | app/routes/connections_contracts.py |
| ConnectionTokenStatus | BaseModel | expires_at: str | None, refreshed_at: str | None | app/routes/connections_contracts.py |
| ConnectionView | BaseModel | key: str, type: str, display_name: str, config: dict[str, Any], enabled: bool... | app/routes/connections_contracts.py |
| ConnectionListResponse | BaseModel | connections: list[ConnectionView] | app/routes/connections_contracts.py |
| ConnectionTypeView | BaseModel | type: str, description: str, required_config_keys: list[str], secret_keys: li... | app/routes/connections_contracts.py |
| ConnectionTypesResponse | BaseModel | types: list[ConnectionTypeView] | app/routes/connections_contracts.py |
| ConnectionTestResponse | BaseModel | ok: bool, message: str | app/routes/connections_contracts.py |
| FailedNodeRunItem | BaseModel | job_id: str, node_key: str, node_run_id: int, workflow_key: str, failure_cate... | app/routes/failed_node_run_contracts.py |
| FailedNodeRunsResponse | BaseModel | runs: list[FailedNodeRunItem] | app/routes/failed_node_run_contracts.py |
| InstanceOpenClawSettings | BaseModel | cwd: str | app/routes/instance_openclaw_contracts.py |
| InstanceCleanupSettings | BaseModel | log_retention_days: int, run_dir_retention_days: int, interval_seconds: int | app/routes/instance_settings_contracts.py |
| InstanceMonitoringSettings | BaseModel | sample_interval_seconds: float, retention_days: int | app/routes/instance_settings_contracts.py |
| InstanceWorkflowsSettings | BaseModel | enabled: bool | app/routes/instance_settings_contracts.py |
| InstanceAgentWorkersSettings | BaseModel | max_archive_bytes: int, min_protocol_version: int | app/routes/instance_settings_contracts.py |
| InstanceSettingsDocument | BaseModel | cleanup: InstanceCleanupSettings, monitoring: InstanceMonitoringSettings, hea... | app/routes/instance_settings_contracts.py |
| JobFilterPayload | BaseModel | status: str | None, search: str | None, workflow_version: int | None, workflo... | app/routes/job_batch_filter_contracts.py |
| JobSelectionMixin | BaseModel | job_ids: list[str] | None, filter: JobFilterPayload | None, exclude_ids: list... | app/routes/job_batch_filter_contracts.py |
| JobBatchRequest | BaseModel | workflow_key: str, entity: str | None, source_kind: str, question_ids: list[s... | app/routes/job_contracts.py |
| JobBatchResponse | BaseModel | batch: dict[str, Any], created_count: int, jobs: list[dict[str, Any]] | app/routes/job_contracts.py |
| WorkspaceCreateRequest | BaseModel | id: str, name: str, default_entity: str, resource_config: dict[str, Any] | app/routes/job_contracts.py |
| WorkspaceUpdateRequest | BaseModel | name: str | None, description: str | None, default_entity: str | None, resour... | app/routes/job_contracts.py |
| WorkspaceSettingsResponse | BaseModel | settings: dict[str, Any] | app/routes/job_contracts.py |
| WorkspaceSettingsSectionRequest | BaseModel | entityType: str | None, workflowKey: str | None, nodeConfig: dict[str, dict[s... | app/routes/job_contracts.py |
| WorkspaceResponse | BaseModel | workspace: WorkspaceRecord | app/routes/job_contracts.py |
| WorkspacesResponse | BaseModel | workspaces: list[WorkspaceRecord] | app/routes/job_contracts.py |
| DeleteJobResponse | BaseModel | deleted: str | app/routes/job_contracts.py |
| ArtifactResponse | BaseModel | name: str, content: str | app/routes/job_contracts.py |
| WorkspaceRunsResponse | BaseModel | runs: list[dict[str, Any]] | app/routes/job_contracts.py |
| WorkspaceDagResponse | BaseModel | workflow: dict[str, Any], nodes: list[dict[str, Any]] | app/routes/job_contracts.py |
| CodePoolStatus | BaseModel | capacity: int, running: int, available: int | app/routes/job_contracts.py |
| WorkspaceStatsResponse | BaseModel | workspace_id: str, name: str, workflow_key: str, workflow_label: str, job_sta... | app/routes/job_contracts.py |
| DeleteWorkspaceResponse | BaseModel | deleted: str | app/routes/job_contracts.py |
| ExecutionControlSummaryResponse | BaseModel | mode: Literal['full', 'until_node'], target_node_key: str | None, paused: boo... | app/routes/job_execution_control_contracts.py |
| JobsPageResponse | BaseModel | workspace_id: str, revision: int, total: int | None, stats: dict[str, int], j... | app/routes/job_list_contracts.py |
| JobFacetsResponse | BaseModel | workspace_id: str, total: int, status_counts: dict[str, int], version_counts:... | app/routes/job_list_contracts.py |
| JobMutationResultResponse | BaseModel | job_id: str, operation: Literal['rerun', 'run_to', 'continue', 'delete', 'pac... | app/routes/job_operation_contracts.py |
| BatchJobMutationResponse | BaseModel | results: list[JobMutationResultResponse] | app/routes/job_operation_contracts.py |
| RunToRequest | BaseModel | target_node_key: str, start_node_key: str | None | app/routes/job_operation_contracts.py |
| ContinueJobRequest | BaseModel | — | app/routes/job_operation_contracts.py |
| JobRerunByFailureRequest | BaseModel | category: Literal['technical', 'business', 'unknown'], strategy: Literal['aut... | app/routes/job_rerun_by_failure_contracts.py |
| JobRerunByFailureResponse | BaseModel | results: list[JobRerunByFailureResultResponse] | app/routes/job_rerun_by_failure_contracts.py |
| BatchRerunPreviewResponse | BaseModel | total_count: int, eligible_count: int | app/routes/job_rerun_preview_contracts.py |
| StressEventRecord | BaseModel | job_id: str, kind: str | app/routes/job_stress_events.py |
| StressEventBatchRequest | BaseModel | events: list[StressEventRecord] | app/routes/job_stress_events.py |
| StressEventBatchResponse | BaseModel | recorded: int, recorded_at: float | app/routes/job_stress_events.py |
| JobNodeSummaryResponse | BaseModel | node_key: str, label: str, status: str, error_message: str | app/routes/job_view_contracts.py |
| JobSummaryResponse | BaseModel | id: str, workspace_id: str, workflow_key: str, source_type: str, source_id: s... | app/routes/job_view_contracts.py |
| JobsResponse | BaseModel | jobs: list[JobSummaryResponse] | app/routes/job_view_contracts.py |
| JobsSnapshotResponse | BaseModel | workspace_id: str, revision: int, stats: dict[str, int], jobs: list[JobSummar... | app/routes/job_view_contracts.py |
| JobNodeResponse | BaseModel | id: int, job_id: str, node_key: str, status: str, stale_reason: str, error_me... | app/routes/job_view_contracts.py |
| NodeRunResponse | BaseModel | id: int, job_id: str, node_key: str, status: str, started_at: str, finished_a... | app/routes/job_view_contracts.py |
| LogEventResponse | BaseModel | type: str, title: str, detail: str, truncated: bool | app/routes/job_view_contracts.py |
| JobLogResponse | BaseModel | run_id: int, log: str, truncated: bool, structured: list[LogEventResponse] | ... | app/routes/job_view_contracts.py |
| JobDetailResponse | BaseModel | job: JobSummaryResponse, nodes: list[JobNodeResponse], runs: list[NodeRunResp... | app/routes/job_view_contracts.py |
| MaterialBundleMemberInput | BaseModel | material_id: str, path: str | app/routes/material_bundles.py |
| MaterialBundleCreateRequest | BaseModel | name: str, members: list[MaterialBundleMemberInput] | app/routes/material_bundles.py |
| MaterialBundleMemberRecord | BaseModel | material_id: str, path: str, ordinal: int, filename: str, size_bytes: int, co... | app/routes/material_bundles.py |
| MaterialBundleRecord | BaseModel | id: str, workspace_id: str, name: str, total_size_bytes: int, file_count: int... | app/routes/material_bundles.py |
| MaterialBundleResponse | BaseModel | bundle: MaterialBundleRecord | app/routes/material_bundles.py |
| MaterialBundleListResponse | BaseModel | bundles: list[MaterialBundleRecord], total: int, limit: int, offset: int | app/routes/material_bundles.py |
| MaterialBundleDeleteResponse | BaseModel | deleted: str | app/routes/material_bundles.py |
| MaterialPresignRequest | BaseModel | filename: str, size_bytes: int, content_hash: str | None, content_type: str | app/routes/materials.py |
| MaterialRecord | BaseModel | id: str, workspace_id: str, content_hash: str, filename: str, content_type: s... | app/routes/materials.py |
| MaterialPresignResponse | BaseModel | material: MaterialRecord, upload_url: str | None, upload_expires_in_seconds: ... | app/routes/materials.py |
| MaterialResponse | BaseModel | material: MaterialRecord | app/routes/materials.py |
| MaterialListResponse | BaseModel | materials: list[MaterialRecord], total: int, limit: int, offset: int | app/routes/materials.py |
| MaterialDeleteResponse | BaseModel | deleted: str | app/routes/materials.py |
| MetricBucket | BaseModel | bucket_start: str, online_workers: int, online_workers_max: int, active_execu... | app/routes/metrics_contracts.py |
| OpsMetricsResponse | BaseModel | granularity: Literal['6h', '24h', '30d'], buckets: list[MetricBucket], summar... | app/routes/metrics_contracts.py |
| QueueSummary | BaseModel | queued: int, oldest_queued_at: str | None, recent_hour_unclaimable_failed: int | app/routes/metrics_queue_contracts.py |
| QueueAlert | BaseModel | kind: Literal['blocked', 'stalled'], at: str | None, reasons: dict[str, int] | app/routes/metrics_queue_contracts.py |
| RecentHourTokenSummary | BaseModel | input_tokens: int, output_tokens: int, cache_read_tokens: int, total_tokens: ... | app/routes/metrics_summary_contracts.py |
| RecentHourRunSummary | BaseModel | completed: int, failed: int, duration_p50_seconds: float | None, duration_p95... | app/routes/metrics_summary_contracts.py |
| OpsMetricsSummary | BaseModel | online_workers: int | None, active_executions: int | None, recent_hour_tokens... | app/routes/metrics_summary_contracts.py |
| WorkspacePackageResultResponse | BaseModel | job_id: str, status: Literal['succeeded', 'failed'], reason_code: str | None,... | app/routes/package_contracts.py |
| WorkspacePackageResponse | BaseModel | results: list[WorkspacePackageResultResponse], succeeded_count: int, failed_c... | app/routes/package_contracts.py |
| WorkspacePackageStatusResetResponse | BaseModel | results: list[WorkspacePackageResultResponse], succeeded_count: int, failed_c... | app/routes/package_contracts.py |
| WorkspacePackageUpdate | BaseModel | name: str | None, locked: bool | None | app/routes/package_history_contracts.py |
| WorkspacePackageItemResponse | BaseModel | id: int, name: str, path: str, video_count: int, size_bytes: int, locked: int... | app/routes/package_history_contracts.py |
| WorkspacePackagesResponse | BaseModel | packages: list[WorkspacePackageItemResponse] | app/routes/package_history_contracts.py |
| WorkspacePackageDeleteResponse | BaseModel | deleted: bool | app/routes/package_history_contracts.py |
| WorkspacePackageUpdateResponse | BaseModel | id: int, name: str | None, locked: bool | None | app/routes/package_history_contracts.py |
| QualitySampleFilters | BaseModel | node_keys: list[str] | None, statuses: list[str] | None, since: datetime | No... | app/routes/quality_contracts.py |
| QualitySampleBatchCreateRequest | BaseModel | name: str, workflow_key: str | None, filters: QualitySampleFilters, sample_si... | app/routes/quality_contracts.py |
| QualitySampleBatch | BaseModel | id: str, workspace_id: str, name: str, workflow_key: str, filters: dict[str, ... | app/routes/quality_contracts.py |
| QualitySampleBatchListResponse | BaseModel | batches: list[QualitySampleBatch] | app/routes/quality_contracts.py |
| QualityLabel | BaseModel | id: str, item_id: str, target: str, verdict: str, reason_codes: list[str], no... | app/routes/quality_contracts.py |
| QualitySampleItem | BaseModel | id: str, batch_id: str, node_run_id: int, job_id: str, node_key: str, capabil... | app/routes/quality_contracts.py |
| QualitySampleBatchDetailResponse | BaseModel | batch: QualitySampleBatch, items: list[QualitySampleItem], total: int | app/routes/quality_contracts.py |
| QualityArtifactContent | BaseModel | name: str, content: str, truncated: bool | app/routes/quality_contracts.py |
| QualitySampleItemDetailResponse | BaseModel | item: QualitySampleItem, labels: list[QualityLabel], artifacts: list[QualityA... | app/routes/quality_contracts.py |
| QualityLabelCreateRequest | BaseModel | verdict: LabelVerdict, reason_codes: list[str], note: str, replay_id: str | N... | app/routes/quality_contracts.py |
| QualityLabelResponse | BaseModel | label: QualityLabel | app/routes/quality_contracts.py |
| QualityReplayCreateRequest | BaseModel | agent_version: int | None | app/routes/quality_contracts.py |
| QualityReplay | BaseModel | id: str, item_id: str, agent_id: str, agent_version: int | None, replay_job_i... | app/routes/quality_contracts.py |
| QualityReplayResponse | BaseModel | replay: QualityReplay | app/routes/quality_contracts.py |
| QualityReplayListResponse | BaseModel | replays: list[QualityReplay] | app/routes/quality_contracts.py |
| QualityReplayDetailResponse | BaseModel | replay: QualityReplay, labels: list[QualityLabel], artifacts: list[QualityArt... | app/routes/quality_contracts.py |
| QualityConfusionMatrix | BaseModel | tp: int, fp: int, fn: int, tn: int, precision: float | None, recall: float | ... | app/routes/quality_contracts.py |
| QualityStatsGroup | BaseModel | node_key: str, skill_version: str, provider: str, model: str, runs: int, succ... | app/routes/quality_contracts.py |
| QualityBatchStatsResponse | BaseModel | batch_id: str, groups: list[QualityStatsGroup] | app/routes/quality_contracts.py |
| RunItemMaterial | BaseModel | type: Literal['material'], material_id: str | app/routes/run_contracts.py |
| RunItemRef | BaseModel | type: Literal['ref'], connection_key: str, external_id: str, params: dict[str... | app/routes/run_contracts.py |
| RunItemBundle | BaseModel | type: Literal['bundle'], bundle_id: str | app/routes/run_contracts.py |
| RunCreateRequest | BaseModel | workflow_key: str, items: list[RunItem] | app/routes/run_contracts.py |
| RunRecord | BaseModel | id: str, workspace_id: str, workflow_key: str, source_kind: str, status: str,... | app/routes/run_contracts.py |
| RunCreateResponse | BaseModel | run: RunRecord, created_count: int, jobs: list[dict[str, Any]] | app/routes/run_contracts.py |
| RunListResponse | BaseModel | runs: list[RunRecord] | app/routes/run_contracts.py |
| RunJobStats | BaseModel | total: int, by_status: dict[str, int] | app/routes/run_contracts.py |
| RunDetailResponse | BaseModel | run: RunRecord, job_stats: RunJobStats | app/routes/run_contracts.py |
| SkillFileResponse | BaseModel | path: str, size: int, content: str, truncated: bool | app/routes/skill_contracts.py |
| SkillDetailResponse | BaseModel | key: str, ref: str, commit: str, available: bool, tags: list[str], files: lis... | app/routes/skill_contracts.py |
| SkillValidateRequest | BaseModel | path: str | app/routes/skill_contracts.py |
| SkillValidateResponse | BaseModel | valid: bool, path: str, skill_key: str | None, error: str | None, tags: list[... | app/routes/skill_contracts.py |
| SkillTagsResponse | BaseModel | path: str, tags: list[str], latest_tag: str | None | app/routes/skill_contracts.py |
| SkillSourceEntry | BaseModel | key: str, repo: str, ref: str, locked_commit: str | None, resolved_at: str | ... | app/routes/skill_source_contracts.py |
| SkillSourcesResponse | BaseModel | skills: list[SkillSourceEntry] | app/routes/skill_source_contracts.py |
| SkillSourceUpdate | BaseModel | repo: str, ref: str | app/routes/skill_source_contracts.py |
| StudioContextNode | BaseModel | key: str, capability: str | app/routes/studio_agent_context_contracts.py |
| StudioContextEdge | BaseModel | source: str, target: str | app/routes/studio_agent_context_contracts.py |
| StudioContextWorkflow | BaseModel | workflow_key: str, version: int, nodes: list[StudioContextNode], edges: list[... | app/routes/studio_agent_context_contracts.py |
| StudioChatContextResponse | BaseModel | workspace_id: str, selected_node_key: str | None, draft_yaml: str | None, wor... | app/routes/studio_agent_context_contracts.py |
| SkillValidationIssue | BaseModel | path: str, error: str | app/routes/studio_agent_skill_contracts.py |
| SkillValidateToolResponse | BaseModel | key: str, valid: bool, errors: list[SkillValidationIssue] | app/routes/studio_agent_skill_contracts.py |
| SkillVersionFileWrite | BaseModel | path: str, content: str | app/routes/studio_agent_skill_contracts.py |
| SkillSaveVersionRequest | BaseModel | files: list[SkillVersionFileWrite], new_tag: str, message: str | app/routes/studio_agent_skill_contracts.py |
| SkillSaveVersionResponse | BaseModel | key: str, tag: str, commit: str, files: list[str] | app/routes/studio_agent_skill_contracts.py |
| StudioAgentTokenMintRequest | BaseModel | ttl_hours: int | app/routes/studio_agent_token_contracts.py |
| StudioAgentTokenMintResponse | BaseModel | id: str, token: str, expires_at: str | app/routes/studio_agent_token_contracts.py |
| StudioAgentTokenEntry | BaseModel | id: str, created_at: str, expires_at: str, revoked_at: str | None | app/routes/studio_agent_token_contracts.py |
| StudioAgentTokensResponse | BaseModel | tokens: list[StudioAgentTokenEntry] | app/routes/studio_agent_token_contracts.py |
| StudioAgentTokenRevokeResponse | BaseModel | id: str, revoked: bool | app/routes/studio_agent_token_contracts.py |
| StudioAgentActiveWorkflowResponse | BaseModel | state: Literal['active', 'empty'], workflow_key: str | None, revision: Workfl... | app/routes/studio_agent_tool_contracts.py |
| StudioAgentRegistryEntry | BaseModel | id: str, label: str, command: str, args: list[str] | app/routes/studio_agents_admin_contracts.py |
| StudioAgentRegistryDocument | BaseModel | api_base: str, agents: list[StudioAgentRegistryEntry] | app/routes/studio_agents_admin_contracts.py |
| StudioChatAgentOption | BaseModel | id: str, label: str | app/routes/studio_chat_contracts.py |
| StudioChatAgentsResponse | BaseModel | agents: list[StudioChatAgentOption] | app/routes/studio_chat_contracts.py |
| StudioChatSessionCreateRequest | BaseModel | agent_id: str, title: str | app/routes/studio_chat_contracts.py |
| StudioChatSessionRecord | BaseModel | id: str, workspace_id: str, user_id: str, agent_id: str, title: str, status: ... | app/routes/studio_chat_contracts.py |
| StudioChatSessionResponse | BaseModel | session: StudioChatSessionRecord | app/routes/studio_chat_contracts.py |
| StudioChatSessionsResponse | BaseModel | sessions: list[StudioChatSessionRecord] | app/routes/studio_chat_contracts.py |
| StudioChatMessageCreateRequest | BaseModel | text: str | app/routes/studio_chat_contracts.py |
| StudioChatMessageRecord | BaseModel | id: str, session_id: str, kind: MessageKind, role: MessageRole, content: dict... | app/routes/studio_chat_contracts.py |
| StudioChatMessageResponse | BaseModel | message: StudioChatMessageRecord | app/routes/studio_chat_contracts.py |
| StudioChatMessagesResponse | BaseModel | messages: list[StudioChatMessageRecord] | app/routes/studio_chat_contracts.py |
| StudioChatAllowAllRequest | BaseModel | enabled: bool | app/routes/studio_chat_contracts.py |
| StudioChatContextUpdateRequest | BaseModel | selected_node_key: str | None, draft_yaml: str | None | app/routes/studio_chat_contracts.py |
| StudioChatPermissionAnswerRequest | BaseModel | option_id: str | None, deny: bool | app/routes/studio_chat_contracts.py |
| StudioChatPermissionAnswerResponse | BaseModel | resolved: str | app/routes/studio_chat_contracts.py |
| TokenUsageRunItem | BaseModel | run_id: int, node_key: str, status: str, usage: RunUsage | None, reason: str ... | app/routes/token_usage_contracts.py |
| TokenUsageTotal | BaseModel | message_count: int, input_tokens: int, output_tokens: int, cache_read_tokens:... | app/routes/token_usage_contracts.py |
| TokenUsageJobResponse | BaseModel | job_id: str, runs: list[TokenUsageRunItem], total: TokenUsageTotal, runs_with... | app/routes/token_usage_contracts.py |
| TokenUsageWorkspaceResponse | BaseModel | workspace_id: str, currency: str, summary: TokenUsageSummary, groups: list[To... | app/routes/token_usage_contracts.py |
| TokenUsagePricingRate | BaseModel | provider: str, model: str, input_per_1m: float, output_per_1m: float, cache_r... | app/routes/token_usage_pricing_contracts.py |
| TokenUsagePricingConfigResponse | BaseModel | currency: str, pricing: list[TokenUsagePricingRate] | app/routes/token_usage_pricing_contracts.py |
| TokenUsagePricingConfigUpdate | BaseModel | currency: str, pricing: list[TokenUsagePricingRate] | app/routes/token_usage_pricing_contracts.py |
| RunUsageCost | BaseModel | currency: str, input: float | None, output: float | None, cache_read: float |... | app/routes/token_usage_run_contracts.py |
| RunUsage | BaseModel | node_run_id: int, node_key: str, provider: str, model: str, skill_version: st... | app/routes/token_usage_run_contracts.py |
| TokenUsageCostBreakdown | BaseModel | currency: str, input: float | None, output: float | None, cache_read: float |... | app/routes/token_usage_run_contracts.py |
| TokenUsageRunResponse | BaseModel | job_id: str, run_id: int, usage: RunUsage | None, reason: str | None | app/routes/token_usage_run_contracts.py |
| TokenUsageWorkspaceGroup | BaseModel | group_key: str, node_key: str, provider: str, model: str, skill_version: str,... | app/routes/token_usage_workspace_group_contract.py |
| WorkerStatusResponse | BaseModel | paused: bool | app/routes/worker.py |
| WorkflowSummaryResponse | BaseModel | key: str, label: str | app/routes/workflow_contracts.py |
| WorkflowIntakeModeResponse | BaseModel | key: str, label: str, input_field: str | app/routes/workflow_contracts.py |
| WorkflowIntakeResponse | BaseModel | modes: list[WorkflowIntakeModeResponse] | app/routes/workflow_contracts.py |
| WorkflowConditionResponse | BaseModel | artifact: str, path: str, equals: Any | app/routes/workflow_contracts.py |
| WorkflowEdgeResponse | BaseModel | source: str, target: str, condition: WorkflowConditionResponse | None | app/routes/workflow_contracts.py |
| WorkflowResponse | BaseModel | workflow: WorkflowDefinitionResponse | app/routes/workflow_contracts.py |
| WorkflowDraftCompareRequest | BaseModel | definition_yaml: str, allow_missing_baseline: bool | app/routes/workflow_draft_compare_contracts.py |
| WorkflowDraftCompareError | BaseModel | category: str, message: str, line: int | None, column: int | None, node_key: ... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowRevisionSummaryItem | BaseModel | id: str, version: int, workflow_key: str, definition_hash: str | app/routes/workflow_draft_compare_contracts.py |
| WorkflowDraftSummaryItem | BaseModel | key: str, label: str, version: int | app/routes/workflow_draft_compare_contracts.py |
| WorkflowNodeChange | BaseModel | type: WorkflowChangeType, node_key: str, label: str, node_type: Literal['star... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowEdgeChange | BaseModel | type: WorkflowEdgeChangeType, source: str, target: str, before_condition: str... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowIntakeChange | BaseModel | type: WorkflowIntakeChangeType, mode_key: str, field_key: str | None, risk: W... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowRiskFlag | BaseModel | code: str, severity: WorkflowRiskLevel, message: str | app/routes/workflow_draft_compare_contracts.py |
| WorkflowCompareSummary | BaseModel | risk_level: WorkflowRiskLevel, node_changes: list[WorkflowNodeChange], edge_c... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowDraftCompareResponse | BaseModel | valid: bool, creates_revision: bool, base_revision: WorkflowRevisionSummaryIt... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowMetadataChange | BaseModel | type: Literal['modified'], field: str, before_value: str | None, after_value:... | app/routes/workflow_draft_compare_metadata_contracts.py |
| WorkflowDraftStoreRequest | BaseModel | definition_yaml: str | app/routes/workflow_draft_store_contracts.py |
| WorkflowDraftStoreResponse | BaseModel | definition_yaml: str | None, updated_at: str | None | app/routes/workflow_draft_store_contracts.py |
| WorkflowNodeCodeResponse | BaseModel | origin: Literal['builtin', 'custom', 'none'], code: str, version: int | None,... | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeTemplateResponse | BaseModel | code: str | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeDraftRequest | BaseModel | code: str, change_note: str | None | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeVersionResponse | BaseModel | id: str, version: int, status: str, code: str, code_hash: str, created_by: st... | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeVersionSummary | BaseModel | id: str, version: int, status: str, code_hash: str, created_by: str, change_n... | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeVersionsResponse | BaseModel | versions: list[WorkflowNodeCodeVersionSummary] | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeRollbackRequest | BaseModel | version: int | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeArchiveResponse | BaseModel | archived: int | app/routes/workflow_node_code_contracts.py |
| WorkflowTerminalResponse | BaseModel | outcome: str | app/routes/workflow_node_contracts.py |
| WorkflowNodeExecutionResponse | BaseModel | provider: str, model: str, thinking: str, prompt: str | app/routes/workflow_node_contracts.py |
| WorkflowNodeResponse | BaseModel | key: str, label: str, capability: str, node_type: str, accepted_item_types: l... | app/routes/workflow_node_contracts.py |
| WorkflowRevisionSummary | BaseModel | id: str, workspace_id: str, workflow_key: str, version: int, status: str, def... | app/routes/workflow_revisions_contracts.py |
| WorkflowRevisionsResponse | BaseModel | revisions: list[WorkflowRevisionSummary] | app/routes/workflow_revisions_contracts.py |
| WorkflowDraftRequest | BaseModel | definition_yaml: str | app/routes/workflow_revisions_contracts.py |
| WorkflowDraftValidationResponse | BaseModel | valid: bool, errors: list[str] | app/routes/workflow_revisions_contracts.py |
| ActiveWorkflowRevisionResponse | BaseModel | revision: WorkflowRevisionSummary, workflow: workflow_contracts.WorkflowDefin... | app/routes/workflow_revisions_contracts.py |
| WorkflowRevisionDetailResponse | BaseModel | revision: WorkflowRevisionSummary, workflow: workflow_contracts.WorkflowDefin... | app/routes/workflow_revisions_contracts.py |
| WorkspaceRecord | BaseModel | id: str, name: str, description: str, default_workflow_key: str, default_enti... | app/routes/workspace_contracts.py |
| NodeLimitRequest | BaseModel | workflow_key: str, node_key: str, concurrency_limit: int | app/routes/workspace_execution_contracts.py |
| WorkspaceExecutionConfigurationResponse | BaseModel | node_limits: list[NodeLimitRequest], migration_warnings: list[str], agent_cap... | app/routes/workspace_execution_contracts.py |
| WorkspaceAgentRouteEntry | BaseModel | workflow_key: str, node_key: str, node_label: str, capability: str, agent_id:... | app/routes/workspace_execution_contracts.py |
| WorkspaceAgentRoutesResponse | BaseModel | routes: list[WorkspaceAgentRouteEntry] | app/routes/workspace_execution_contracts.py |
| WorkspaceSettingsPayload | BaseModel | entityType: str, workflowKey: str, previewHidden: list[str] | app/routes/workspace_execution_contracts.py |
| WorkspaceConfigurationSettingsRequest | BaseModel | entityType: str | None, workflowKey: str | None, previewHidden: list[str] | N... | app/routes/workspace_execution_contracts.py |
| WorkspaceConfigurationRequest | BaseModel | name: str | None, description: str | None, settings: WorkspaceConfigurationSe... | app/routes/workspace_execution_contracts.py |
| WorkspaceConfigurationResponse | BaseModel | workspace: WorkspaceRecord, settings: WorkspaceSettingsPayload, execution_con... | app/routes/workspace_execution_contracts.py |
| WorkspaceRuntimeModelsResponse | BaseModel | runtimes: dict[str, dict[str, list[str]]] | app/routes/workspace_runtime_models.py |
| WorkspaceSecretSetRequest | BaseModel | value: str | app/routes/workspace_secrets.py |
| WorkspaceSecretMetadata | BaseModel | name: str, created_at: str, updated_at: str | app/routes/workspace_secrets.py |
| WorkspaceSecretsResponse | BaseModel | secrets: list[WorkspaceSecretMetadata] | app/routes/workspace_secrets.py |
| WorkspaceSecretResponse | BaseModel | secret: WorkspaceSecretMetadata | app/routes/workspace_secrets.py |
| WorkspaceSecretDeleteResponse | BaseModel | deleted: str | app/routes/workspace_secrets.py |
| JobDeleteResult | TypedDict | job_id: str, operation: str, status: str, reason_code: str | None, message: s... | app/services/job_deletion.py |
| LogEntry | TypedDict | type: str, title: str, detail: str, truncated: bool | app/services/job_log_renderer.py |
| JobOperationResult | TypedDict | job_id: str, operation: str, status: str, node_key: str | None, reason_code: ... | app/services/job_operation_error.py |
| CostBreakdown | BaseModel | currency: str, input: float, output: float, cache_read: float, total: float, ... | app/services/token_usage_pricing.py |
| JobPackageItemResult | TypedDict | job_id: str, status: str, reason_code: str | None, message: str | None | app/services/workspace_package_contracts.py |
| JobPackageResult | TypedDict | results: list[JobPackageItemResult], succeeded_count: int, failed_count: int,... | app/services/workspace_package_contracts.py |
| SkillSourceConfig | BaseModel | repo: str, ref: str | app/skills/config.py |
| SkillsConfig | BaseModel | skills: dict[str, SkillSourceConfig] | app/skills/config.py |
| LockedSkillSource | BaseModel | repo: str, ref: str, commit: str | app/skills/config.py |
| SkillsLock | BaseModel | version: str, resolved_at: str | None, skills: dict[str, LockedSkillSource] | app/skills/config.py |

<!-- END AUTO-GENERATED -->

## 接口契约与架构守护

- FastAPI 路由必须使用 Pydantic 响应模型，它们是 HTTP 接口的唯一事实来源。
- `scripts/export_openapi.py` 在不启动 Worker 的情况下导出 OpenAPI 模式。
- `frontend/src/generated/api.ts` 由 OpenAPI 模式生成，并通过 `npm run api:check` 做漂移检查；禁止手写重复的传输类型。
- `scripts/check_architecture.py` 在质量门禁中执行，负责约束模块边界与体积预算。
- 源文件体积预算由 `config/architecture/architecture-budget-policy.yaml`（人工维护的策略）和
  `config/architecture/architecture-budgets.json`（机器维护的基线）共同治理。基线通过 ratchet 脚本更新：

  ```bash
  UV_CACHE_DIR=.uv-cache uv run python -m scripts.ratchet_architecture_budgets
  UV_CACHE_DIR=.uv-cache uv run python -m scripts.check_architecture
  ```

  ratchet 脚本不会提高 ceiling（`--rebase` / `--bump` 上抬通道已随 #209 移除）；
  `scripts/architecture/budget_monotonicity.py` 在 `check_architecture` 中按 git 锚点
  （HEAD / HEAD^）对照近期提交的基线与豁免冻结值，拒绝**已跟踪条目**的任何
  ceiling 上抬——手工改 `architecture-budgets.json` 抬高数值、或抬高豁免 ceiling
  同样会被拒绝。ceiling 上抬的唯一合法通道是带 `remove_when` 的
  `architecture.file_budget` 豁免。改名绕过已随 #236 加固：检测交给 git 自身的
  rename 相似度引擎（`git diff --find-renames`，含未提交改动），命中的新路径沿用
  旧路径地板——改名不再重置 ceiling，真正的全新文件首次登记
  （actual + buffer）不受约束（删旧建新的正常重构不会误判，因为只有 git 判定
  内容相似才配对）；深克隆缺锚点时硬失败，git 超时/仓库损坏会在错误中携带真实
  原因（不再是纯浅克隆猜测），逃生口 env
  `AGENT_LEGION_BUDGET_MONOTONICITY_SHALLOW=1`。超出预算的文件必须拆分或回退。
  ceiling 按有效行数计
  （排除注释行与空行，实现见 `scripts/architecture/effective_lines.py`），压缩注释
  对预算没有帮助。此外 production 文件有
  800 行绝对上限（`production.max_lines`，按原始行数计），豁免也不能突破；挂账超过 30 天的豁免由
  `scripts/check_exemption_age.py` 在 full gate 中告警（不阻断）。

### Agent Worker 协议响应形态（response model 豁免的依据）

两条 Agent Worker 协议端点按协议设计没有 JSON response model 可命名，
是 `architecture.route_response_model` 检查的长期豁免（锚定本节）：

- `POST /api/agent-executions/{execution_id}/result`（routes/agent_workers.py）：结果上报
  的确认按协议返回空 body 的 204 响应（`Response(status_code=204)`），无 JSON 可建模。
- `POST /api/agent-executions/{execution_id}/release-slot`（routes/agent_workers.py）：
  释放槽位的确认同样按协议返回空 204 响应，无 body。

## Runtime Architecture

### 后端

- `server.app.main:create_app(data_dir, start_worker)` 是 FastAPI 应用工厂。
- 当 `start_worker=True` 时，生命周期内启动 `WorkflowWorkerThread`：
  - 在 DB 实例设置 `workflows.enabled` 为 `true` 时轮询 Agent Legion DAG 任务。
  - 节点按 capability 分发：DB 中按 workspace 发布的 code 节点（EXEC-CODE-002/003，demo 节点在 workspace 初始化时注入）进入本地 code 池或 Worker code 池；agent 节点（pi / velites runtime）经 broker 派发给 Worker。
- 调度暂停是 **workspace 级**状态：每个 workspace 默认暂停，恢复经
  `POST /api/worker/resume?workspace_id=<id>`（或对应控制台开关）开始处理。
- 后端每次启动会把全部 workspace 重置为暂停（刻意设计，防失控自跑）；恢复调度走
  `scripts/resume-workspaces.sh`（必须在后端首次启动建表之后执行才生效）或在控制台手动恢复。
- 内置示例 workflow `education_video_problems_generation` 的节点序列
  （完整 DAG 定义见 `server/app/workflows/builtin_demo.py`）：

  1. `intake_knowledge_points` — code 节点，读取知识点目录并展开 job 输入
  2. `write_script` — agent 节点，生成教学视频脚本
  3. `review_script` — agent 节点，评审脚本
  4. `generate_questions` — agent 节点，生成练习题（与 `write_script` /
     `review_script` 并行，均依赖 `intake_knowledge_points`）
  5. `review_questions` — agent 节点，评审题目
  6. `publish_content` — code 节点，汇总产物为 `publish_payload.json`

- 任一 node 失败会把 Job 置为 `failed`，错误写入数据库与日志文件。
- 支持从任意 node 重跑；重跑会清除该 node 及下游所有 artifacts。
- `DELETE /api/jobs/{job_id}` 会级联删除 Job 记录、`node_runs`、本地 Job 目录与日志；删除服务还会快照 `job_artifacts` 清单行并删除对象存储副本（`server/app/services/job_deletion.py`）。

### Job Intake 资源解析（resolve phase）

Intake 模式的候选解析由 `server/app/services/job_intake_registry.py` 的 `RESOLVERS` 声明式注册表决定，每个 `(entity, mode)` 对应一个 `ResolverSpec`（`phase` / `resource_key` / `handler`）。平台只内置 direct resolver（`phase=None`，不访问外部资源，按输入值直接 fan-out）；需要访问外部系统的解析一律下沉到 DAG 首节点执行期（节点 config + 实例级外部服务连接注入，见下文 Secrets Vault 的运行时解析），intake 本身不感知业务实体类型。

接入新内容类型只需两步：在 `RESOLVERS` 注册 resolver、为 DAG 首节点绑定 capability 并在其 `config_schema` 声明 `connection` 键（实例级外部服务连接 key）与业务参数。Intake 快照只冻结 `node_config` 与 `secret_ref`；声明 `runtime_mutable: true` 的运行开关键不受冻结约束，每次 dispatch 按同一解析链重取 workspace 覆盖并落 `node_runs.config_snapshot_json` 审计（CONFIG-RUNTIME-MUTABLE-001）。

## Database

- PostgreSQL 服务 Agent Legion workflow 与平台状态（当前版本见 `server/app/db/schema.py` 的 `SCHEMA_VERSION`）：
  - `workspaces` — Agent Legion workspace 定义（含 `default_workflow_key`（schema v62 起 = workspace id，deprecated，见 DB-WORKSPACE-KEY-BINDING-001）, `node_config_json`, `default_entity`, `preview_config_json`（v63，产物预览隐藏列表 `{"hidden": [...]}`，见 Job Detail 预览段）；`intake_config_json` 与 `default_agent_*` 三列已随 schema v64 退役 drop）。`node_config_json` 里 schema 标记 `secret: true` 的字段只存 `{"secret_ref": "<name>"}` 引用，明文不落库（见下文 Secrets Vault）；旧 `resource_config_json`（resource binding）已在 v24 迁移为节点覆盖并清空
  - `workspace_secrets` — vault 加密存储的 workspace 密钥（Fernet 密文，`(workspace_id, name)` 唯一，v16 新增）
  - `external_connections` / `instance_secrets` / `connection_tokens` — 实例级外部服务连接：连接只存非敏感配置，敏感字段 Fernet 加密入 `instance_secrets`（`conn:<key>:<field>` 引用），鉴权 token 加密缓存在 `connection_tokens`（v34 新增，见下文外部服务连接段）
  - `runs`, `jobs`, `job_nodes`, `node_runs` — DAG job 相关表（`job_batches` 已随 schema v53 drop，由 `runs` 取代）
  - `materials` — 材料（单文件条目）元数据；`material_bundles` / `material_bundle_members` — bundle 文件夹条目的冻结引用式清单（schema v55）
  - `job_artifacts` — Job 产物清单（权威副本在实例对象存储，schema v54）
  - `workflow_revisions` — workflow 版本修订历史
  - `workspace_packages` — 已创建 package 路径
- 初始化器在 PostgreSQL advisory lock 下按版本应用 schema。数据迁移经
  `server/app/db/migration_registry.py` 的 `MIGRATIONS` 注册表按版本有序应用
  （DB-SCHEMA-001）；`db/migrations/__init__.py` 的平铺 re-export 随版本每次
  +2 行，长期收敛方向是从注册表派生该导出。
- `JobQueries.connect()` 是上下文管理器（定义在 `JobQueriesBase`），确保 `conn.close()`；workspace 侧查询由 `WorkspaceQueriesMixin` 合并进统一的 `JobQueries`。
- `JobDeletionService` 级联删除 Job 记录、`node_runs`、本地 Job 目录与日志；同时快照 `job_artifacts` 清单行并删除对象存储副本（`server/app/services/job_deletion.py`）。
- 存储路径以**相对 POSIX 路径**保存在 `settings.data_dir` 下（前缀为 `videos/`, `jobs/`, `logs/`, `packages/`），API 返回时投影为绝对路径。
- SQL 占位符约定：**新 SQL 一律写 psycopg 的 `%s`**，不要再写 SQLite 风格的 `?`。存量 `?` 由 `server/app/db/dialect.py` 盲替换为 `%s`，该层无法区分占位符与 Postgres JSON 的 `?`/`?|`/`?&` 操作符；`scripts/check_architecture.py` 的 SQL 占位符检查（基线 `config/architecture/sql-placeholders-baseline.json`）按 ratchet 方式只降不升，新文件出现任何 SQL `?` 即失败，改写存量后同步下调基线。
- 服务层数据边界（BOUNDARY-DATA-001）：`server/app/services/` 下的新服务**必须经 `JobQueries` 门面访问数据库**（范式见 `services/job_pause.py` 等 38+ 个 facade-only 服务）；裸 SQL 字面量与 `server.app.db.transaction`/`connection` 直接 import 由 `scripts/architecture/service_data_boundary.py` 检查冻结（基线 `config/architecture/service-data-boundary-baseline.json`，只降不升）。存量服务迁移到门面后手动（或重跑 ratchet）下调基线；新文件出现任一绕行即失败。

## New Subsystems

### Workflow Studio & Workflow Revisions

Workflow Studio 提供可视化 workflow 编辑能力，与版本修订历史集成。

- **Routes**: `routes/workflow_revisions.py`, `routes/workflow_draft_compare.py`
- **Services**: `services/workflow_drafts.py`, `services/workflow_draft_publish.py`, `services/workflow_revision_format.py`, `services/job_workflow_versions.py`, `services/job_workflow_upgrade.py`; `/api/agent-catalog` 同时返回已发布 Agent Catalog 投影（versioned_entities），供编辑器按 capability 获取 runtime、skill、tools；provider/model/thinking 的「继承默认」提示读 Studio 草稿 YAML 的顶层 `execution` 块（workspace 级 Agent 默认已随 schema v64 退役），可 claim 的选项来自 `GET /api/workspaces/{id}/runtime-models`（在线 Worker 声明聚合）
- **DB**: PostgreSQL `workflow_revisions` 表与版本化 schema 初始化
- **Frontend**: `pages/WorkflowStudioPage.tsx`, `features/workflowStudio/`

### Token Usage

Token Usage 收集并展示 Pi agent 节点运行时的 token 消耗与成本。

- **Routes**: `routes/token_usage.py` (`/jobs/{job_id}/token-usage`, `/jobs/{job_id}/runs/{run_id}/token-usage`, `/workspaces/{workspace_id}/token-usage`)
- **Services**: `services/token_usage*.py`
- **Config**: 全局设置 `global_settings` 表（`token_usage` 文档：currency + pricing），经 `GET/PUT /api/admin/token-usage-pricing` 维护；不再走 yaml
- **Frontend**: `pages/TokenUsagePage.tsx`, `components/TokenUsage*.tsx`

### Configuration Package

`server/app/configuration/` 负责加载并校验按领域拆分的 YAML 配置。

- `loader.py`:  canonical split 布局已不含任何运行时配置文件（全部退役）；启动时从代码默认值 + env 覆盖合成配置，`config/app.yaml` / `config/workflow.yaml` / `config/agent_legion.yaml` 存在即报错（带迁移指引）。
- `owned_keys.py`: 登记退役文件名与迁移指引（`CONFIG_FILE_KEYS` 已为空——没有任何 split 文件再拥有顶层键）。

### Quality Subsystem

`scripts/quality/` 提供架构不变量与豁免注册表的加载与校验（治理工具，不在 server 运行时路径上）。

- `invariants.py`: 读取 `config/architecture/architecture-invariants.yaml` 并校验。
- `exemptions.py`: 读取 `config/architecture/architecture-exemptions.yaml` 并校验。
- 对应脚本：`scripts/check_invariants.py`。

### Job Detail 产物预览（issue #11）

- **raw 字节端点** `GET /api/jobs/{job_id}/artifacts/{artifact_name}/raw`（`routes/job_artifact_raw.py`）：本地 job_dir 副本走 `FileResponse`（原生 Range，媒体可拖动进度条），仅存对象存储的产物走 64 KiB 分块 `StreamingResponse`（`BackgroundTask` 兜底关流）。**content-type 白名单即安全边界**：仅 image/video/audio/pdf 扩展名映射真实 media type 并 `inline` 渲染，其余（含 .html/.svg——同源渲染即脚本执行面）一律 `application/octet-stream` + `attachment` 强制下载。
- **文本端点**（`{artifact_name:path}`）遇本地二进制产物的 `UnicodeDecodeError` 按 404 降级（字节由 raw 端点负责），不冒泡 500。
- **workspace 级预览配置**：`preview_config_json` 存 `{"hidden": [<artifact name>, ...]}`；settings payload 键 `previewHidden`（默认 `[]` = 全部显示，工作流升级产生的新产物自动可见）。写路径两条：`PATCH /settings/preview` section（job 详情左栏勾选菜单，立即生效）与 `PUT /configuration` 全量保存（设置页 draft；缺省 `previewHidden` = 沿用已存值，旧客户端不抹勾选）。

### Secrets Vault

`server/app/services/vault.py` 提供按 workspace 隔离的凭证保管库（VAULT-SECRET-001）。

- **Routes**: `routes/workspace_secrets.py`（`GET/PUT/DELETE /workspaces/{workspace_id}/secrets[/{name}]`），write-only：GET 只返回 name 与时间戳元数据，任何响应都不含明文或密文。
- **Service**: `VaultService`（Fernet 加解密 + `workspace_secrets` 持久化，明文不跨越服务层边界落盘或出 API）与 `WorkspaceSecretsService`（API 门面）。
- **Master key**: env `AGENT_LEGION_VAULT_MASTER_KEY` / `AGENT_LEGION_VAULT_MASTER_KEY_FILE`（映射到 `vault.master_key` / `vault.master_key_file`）；缺 key 时 server 可启动，但 vault 写操作与 `secret_ref` 解析报错。
- **写入链**: 节点配置保存时，capability `config_schema` 标记 `secret: true` 的字段值转存 vault，节点覆盖只留 `{"secret_ref": "node:{workflow_key}:{node_key}:{field}"}`；settings payload 中 secret 字段只返回 `{"secret_set": bool}`。
- **运行时解析**: `resolve_secret_refs` 在 server 端把 `secret_ref` 解析为明文（仅内存；字符串明文透传为兼容窗口），消费点为 dispatch 执行注入；intake 冻结的是 `secret_ref` 而非明文；`job_logs` 脱敏并入 vault 明文。外部服务连接的消费同样在 dispatch 期：vault 解析之后经 `inject_connection_config`（`server/app/workflow_worker/dispatch_config.py`）按节点 `connection` 键把连接端点配置与缓存 token 注入节点 config（仅内存，不落库、不进 agent manifest）。

## Configuration Reference

运行时配置已全部从 split yaml 退役：`config/app.yaml` / `config/workflow.yaml` / `config/agent_legion.yaml` 存在即启动报错（带迁移指引，见 `server/app/configuration/owned_keys.py`）。有效配置 = 代码默认值 + env 覆盖 + DB 文档。

`config/app.yaml` 已整体退役：bootstrap/安全类键转 env-only，实例级可调配置迁入 DB：

- env-only：`database.url` → `AGENT_LEGION_DATABASE_URL`（唯一权威变量，G4；缺省 `postgresql://127.0.0.1:5432/agent_legion`）；`data_dir` → `AGENT_LEGION_DATA_DIR`（缺省 `data`）；`server.cors` → `AGENT_LEGION_CORS_ALLOW_ORIGINS`（逗号分隔）/ `AGENT_LEGION_CORS_ALLOW_CREDENTIALS`；`agent_workers` 的全局 register token 已随 issue #35 退役（遗留的 `AGENT_LEGION_WORKER_REGISTER_TOKEN[_FILE]` 或 yaml `register_token[_file]` 会让启动直接报错）。
- DB 实例设置（`global_settings` 表 `instance` 文档，`GET/PUT /api/admin/instance-settings`，启动 hydration、重启生效，无运行期热更新）：`cleanup.log_retention_days` / `run_dir_retention_days` / `interval_seconds`（日志与运行目录清理策略）、`monitoring.sample_interval_seconds` / `retention_days`（资源监控采样间隔与保留天数）、`heartbeat_interval_seconds` / `lease_ttl_seconds` / `heartbeat_failure_threshold` / `sweeper_enabled` / `sweeper_interval_seconds`、`workflows.enabled`（是否启用 Agent Legion DAG workflow worker）、`agent_workers.max_archive_bytes` / `min_protocol_version`、`openclaw.cwd`（唯一保留键；退役键 `command_template` / `timeout_seconds` / `isolated_workspace_root` / `skill_safety` 随业务工作流管线一并退役——存量 DB 文档读取时剥离、写入返回 422，explicit 单文件配置里的残留键被忽略，但 `skill_safety.repos[].ref` 仍按 config 治理 G3 启动即报错：ref 以 DB `skill_lock` 文档为唯一权威）。代码默认值 `cwd="."`；`AGENT_LEGION_OPENCLAW_CWD` 作为 env 覆盖优先级高于 DB 文档。

env-only 段：`vault`（master key）与 `auth`（bootstrap admin 密码）不属于任何 split 文件的 owned keys，只能经环境变量注入（`AGENT_LEGION_VAULT_MASTER_KEY[_FILE]`、`AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD`）；写进 yaml 会触发 owned-key 校验报错。数据库 URL 同样由 env 治理：`AGENT_LEGION_DATABASE_URL` 为唯一权威变量（G4）。

外部服务集成走实例级外部服务连接（EXTERNAL-CONNECTION-001），不经全局 yaml 段配置（全局 `cms:` 段已退役，写进任何 split yaml 会撞退役文件校验报错）：连接由 admin 在全局设置「外部服务连接」或 admin API（`GET/POST /api/admin/connections`、`PUT/DELETE /api/admin/connections/{key}`、`POST /api/admin/connections/{key}/test`、`GET /api/admin/connection-types`）维护，存 DB `external_connections`（只存非敏感配置）；敏感字段转入实例 vault（`instance_secrets`，Fernet 加密，连接配置里只留 `conn:<key>:<field>` 引用），鉴权换来的 token 加密缓存在 `connection_tokens`，过期在父连接行锁下单飞刷新（`server/app/services/connection_tokens.py`）。平台内置 `static_bearer` 与通用 `hmac_token`（HMAC 签名换 token）adapter（`server/app/services/connection_adapters.py` / `connection_adapter_hmac.py`）；业务专属鉴权协议随业务节点迁出，不再由平台携带。节点 config 只写 `connection: "<key>"` 引用连接 + 业务参数（出厂默认值声明在 capability 的 `config_schema`，沿「schema defaults → 节点 config → workspace 覆盖」链解析，Settings UI 可改）。env `CMS_*` / `AGENT_LEGION_CMS_TOKEN` 运行时通道已退役：升级后首次启动由 schema v34 迁移（`server/app/db/migrations/external_connections.py`）把 env 凭据与 workspace 节点旧配置收编进连接，此后 env 不再被读取。explicit 单文件配置里出现 `cms.token` / `cms.token_gen` 启动即报错（config 治理 G2）。

`config/workflow.yaml` 的 `executors` 段已随 executor 概念整体退役（P-0.5，schema v47 drop 定义/allocation 两表，EXEC-CODE-POOL-001）：非 Agent 路由节点一律进隐含 code 池，池容量 = 实例设置 `code_capacity`，lease 行写常量 `'code'`；节点级并发经 `workspace_node_limits` 声明。code 节点的可调参数只剩一个声明层——节点 `config_schema:` 块（随 revision 快照版本化），平台保留执行键 `timeout_seconds` / `sandbox_network` 自动合并进每个 code 路由节点的有效 schema。

实例级运行时设置（`agent_workers` 限额、`workflows.enabled`、lease/heartbeat/sweeper 时序）不再出现在 yaml，见上文「DB 实例设置」。

token 用量计价已产品化：定价存于 `global_settings` 表（`token_usage` 文档），由 admin 在「全局设置」页（`GET/PUT /api/admin/token-usage-pricing`）维护，成本按每条 run 的 provider + model 匹配定价逐行计算；不再有任何 yaml 侧配置。

Agent 定义不再经 yaml 配置（`agents:` 段与 `workflows.pi` 块已在 schema v27 退役，出现在 yaml 中启动即报错）：AgentDefinition 存于 `versioned_entities` 表（schema v46 起 workspace 作用域，解析严格限定本 workspace、零全局兜底），经 Studio「Agent 管理」或 `/api/agent-definitions`（`workspace_id` 查询参数）做 draft → publish → archive 生命周期管理；热读路径经 `AgentService` 的短 TTL（5s）published 缓存。执行配置（provider/model/thinking）不含在 AgentDefinition 内，按严格链解析：节点 `execution.*` 覆盖 → workflow 顶层 `execution` 默认（loader 加载时合并进每个非 start 节点）→ 报错（workspace `default_agent_*` 已随 schema v64 退役，无全局兜底）；thinking 可空（空 = runtime 决定）。

其他配置文件：

- 外部 Pi skill 仓库源与固定 commit 已产品化：声明（`{repo, ref}`）与解析后的 commit 锁存 DB `global_settings`（`skill_sources` / `skill_lock` 文档），lock 是 skill ref 的唯一权威（G3）；经 admin API（`GET/PUT /api/admin/skill-sources`、`POST /api/admin/skill-sources/relock`）与 /admin/settings「Skill 源管理」维护，CLI `make skills-lock`（`uv run python -m server.app.skills.lock`）刷新锁。tracked `config/skills.yaml` / `config/skills.lock` 已退役：DB 无记录且文件存在时启动一次性导入并 warning，否则用内置常量（`server/app/skills/builtin_sources.py`）seed，此后文件不再读取。
- 内置 workflow DAG 定义在 `server/app/workflows/builtin.py`（Python 常量，随代码走 git review），Node 只声明 `capability`，不声明 `runner`/`agent`/`skill`；schema v62 起创建 workspace 不再种子模板，demo workspace 由 `make import-demo`（`scripts/seed_demo.py`）提供，其 id 与 key 同为 `education_video_problems_generation`。workflow 没有全局注册表（schema v40 的 `workflow_catalog` 表已于 schema v50 退役，DB-WORKFLOW-CATALOG-001）：workflow 就是 workspace 内部的一份 DAG，权威定义是该 workspace 的 active revision（schema v50 起节点覆盖校验、settings schema、无快照 job 的定义回退、worker 扫描列表全部改读它）。schema v62（DB-WORKSPACE-KEY-BINDING-001）起 workspace id 与 `workspaces.default_workflow_key` 是同一个标识：创建时显式填写、终身不可变（PATCH / PUT configuration 改 key 一律 400，发布草稿 key 不匹配 422）；v62 迁移把存量 workspace 的 id 改成已绑定的 key（key 为空的按 id 回填），`default_workflow_key` 作为独立概念已标 deprecated（退役评估 issue 待开）。
- `config/agent-worker.example.yaml`：Worker Service 引导配置样例（`host_url` / `worker_id` / `disabled_runtimes` / `capabilities` / `max_concurrency` 等），Worker 侧独立加载，不经 server 的 owned-key 校验。
- `config/architecture/*`：架构不变量、豁免、源文件体积预算。

## Testing

- 测试位于 `tests/`，使用 pytest。
- `pyproject.toml` 配置 `pythonpath = ["."]`，支持 `server.app.db` 这类导入。
- 覆盖率阈值 `fail_under = 85`（`pyproject.toml`）。
- API 测试使用 `fastapi.testclient.TestClient`，`client` fixture 必须 `with TestClient(app) as c:`。

常用命令：

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q
UV_CACHE_DIR=.uv-cache uv run pytest -q --cov=server --cov-report=term-missing
```

## Security Considerations

- 节点代码执行统一在 `velites sandbox wrap` OS 沙箱（seatbelt / bubblewrap）内进行，网络默认拒绝，文件系统默认只放行 job_dir、`/tmp` 与显式 allow-list；沙箱后端不可用时执行 fail-closed（EXEC-CODE-003）。
- OpenClaw runtime 当前未实现；如未来启用，其命令模板应来自 DB 实例设置文档（`/api/admin/instance-settings`，仅管理员可写），替换前经 null 字节剔除与 `shlex.quote` 清洗。
- PostgreSQL 与文件存储部署在受信网络内；业务 API 均需登录（cookie session 或 Bearer token，见 README 的「快速开始 / 登录」章节），uvicorn 默认绑定 127.0.0.1，启动脚本与 Makefile 均显式固定 `--host 127.0.0.1`。不要用 `--host 0.0.0.0` 把开发服务器暴露到局域网或任何不可信网络——暴露后任何通过鉴权的用户都可删除 job、下载产物、触发执行。
- Workspace 凭证经 vault 加密落库（`workspace_secrets`，Fernet），API 永不返回明文，配置与 intake 快照只存 `secret_ref`；实例级外部服务连接凭据与鉴权 token 同样 Fernet 加密落 `instance_secrets` / `connection_tokens`（实例 vault），只在 dispatch 注入与连接探测时于内存解析；master key 走 env / 文件注入，不进 DB、不进日志（VAULT-SECRET-001）。
- `data/` 已加入 `.gitignore`，禁止提交运行时数据或密钥。
