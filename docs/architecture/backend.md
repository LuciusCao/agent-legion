# 后端架构

## Overview

Agent Legion 后端基于 FastAPI，提供 REST API、SSE 事件推送和 WebSocket Agent 状态。核心职责包括：

- Agent Legion DAG 工作流执行（Workspace / Job / Node）
- 视频流水线（ intake → 下载 → 转录 → Agent 阶段 → 打包）
- CMS 集成（知识库与题库查询）
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
│   ├── questions.py        # 题目详情查询
│   ├── skill_catalog_route.py # Skill 目录查询
│   ├── token_usage.py      # Token 用量统计
│   ├── video_jobs*.py      # 视频 Job 详情与源文件
│   ├── worker.py           # Worker 控制（暂停/恢复）
│   ├── workflow_*.py       # 工作流目录、修订、草稿对比
│   ├── workspace_*.py      # Workspace、执行器、设置
│   └── __init__.py         # 路由组装
├── services/               # 业务逻辑服务层
│   ├── job_*.py            # Job 查询、执行、重跑、删除、打包
│   ├── question_detail.py  # Question CMS 集成与关联 Job 聚合
│   ├── token_usage*.py     # Token 用量统计与定价
│   ├── workflow_*.py       # 工作流草稿、修订、格式转换
│   ├── workspace_*.py      # Workspace 配置与执行器配置
│   └── ...
├── pipeline/               # 视频处理流水线阶段
│   ├── download.py         # HTTP 下载
│   ├── transcribe.py       # ASR 转录
│   ├── assemble.py         # 元数据组装
│   └── package.py          # ZIP 打包
├── workflows/              # Agent Legion DAG 定义与执行
│   ├── definition.py       # 工作流定义解析
│   ├── scheduler.py        # DAG 调度
│   ├── workflow_node_execution.py # 节点执行
│   ├── pi_runner.py        # Pi Agent 运行器
│   ├── skills.py           # Skill 路径解析 / 契约检查
│   ├── question_comprehension_info.py
│   ├── video_knowledge.py
│   └── ...
├── db/                     # 数据库层
│   └── schema.py           # 表结构定义
├── jobs/                   # Job 领域查询与类型
│   └── queries/            # JobQueries（含 WorkspaceQueriesMixin）等
├── cms/                    # CMS 客户端
│   ├── auth.py             # 认证
│   ├── client.py           # HTTP 客户端
│   ├── knowledge.py        # 知识库查询
│   └── question.py         # 题库查询
├── configuration/          # 配置加载与 owned-keys 校验
├── video_capabilities/     # 视频能力合约与投影
├── executors/              # Executor 配置、Runtime、租赁调度、registry factory
├── events/                 # 事件总线、Agent 发现与状态跟踪（agents.py）、WS 广播
├── workflow_worker/      # DAG workflow worker：thread.py 线程、ready.py 每 pass
│                         # 一次的 ready 候选收集、schedule.py lease 认领与提交、
│                         # agent_stock.py 产能库存配置
```

## Data Flow

```
客户端请求 → FastAPI Router → Service Layer → DB / Pipeline / CMS / Pi
                     ↓
         SSE Events / WebSocket ← DB Notifications
                     ↓
               前端实时更新
```

`WorkflowWorkerThread` 定期轮询数据库，驱动 Agent Legion DAG Job 从 `queued` 向 `completed` 状态推进；视频 Job 由 workflow 中的 `video_knowledge` handler 执行下载、转录、Agent 阶段与打包。

## Key Decisions

- PostgreSQL 是唯一运行时数据库，通过连接池支撑多进程、多设备并发协调。
- Agent Legion DAG 是主要的执行模型；视频流水线作为 `video_knowledge` workflow 运行。
- 所有文件 I/O 限制在 `data/` 目录内，由 `security.py` 做路径校验。
- 路由、服务、执行器之间有明确的边界：Route 只做 HTTP 适配，Service 处理业务逻辑，Executor 通过租赁（lease）申请容量。详见 [AGENTS.md](../../AGENTS.md)。
- CMS 客户端将网络、响应解析和鉴权失败统一为 `CmsClientError`；业务层只降级明确的
  集成错误，不吞掉编程异常。
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
| GET | `/agent-workers/self/metrics` | `get_worker_metrics` | routes/agent_worker_metrics.py |
| POST | `/agent-workers/register` | `register` | routes/agent_workers.py |
| POST | `/agent-register-tokens` | `create_register_token` | routes/agent_workers.py |
| GET | `/agent-register-tokens` | `list_register_tokens` | routes/agent_workers.py |
| POST | `/agent-register-tokens/{token_id}/revoke` | `revoke_register_token` | routes/agent_workers.py |
| GET | `/agent-workers/self` | `get_worker_self` | routes/agent_workers.py |
| POST | `/agent-workers/{worker_id}/revoke` | `revoke_worker` | routes/agent_workers.py |
| GET | `/agent-workers` | `list_workers` | routes/agent_workers.py |
| POST | `/agent-executions/claim` | `claim` | routes/agent_workers.py |
| GET | `/agent-executions/{execution_id}/bundle` | `bundle` | routes/agent_workers.py |
| POST | `/agent-executions/{execution_id}/heartbeat` | `heartbeat` | routes/agent_workers.py |
| POST | `/agent-executions/{execution_id}/release-slot` | `release_slot` | routes/agent_workers.py |
| POST | `/agent-executions/{execution_id}/result` | `result` | routes/agent_workers.py |
| GET | `/agents` | `list_agents` | routes/agents.py |
| WEBSOCKET | `/agents` | `agents_ws` | routes/agents.py |
| POST | `/artifacts` | `upload_artifact` | routes/artifacts.py |
| GET | `/artifacts/{hash}` | `download_artifact` | routes/artifacts.py |
| GET | `/health` | `health` | routes/common.py |
| GET | `/dashboard/events` | `dashboard_events` | routes/dashboard_events.py |
| GET | `/executor-definitions` | `list_executor_definitions` | routes/executor_definitions.py |
| POST | `/executor-definitions` | `create_executor_definition` | routes/executor_definitions.py |
| GET | `/executor-definitions/{executor_id}` | `get_executor_definition` | routes/executor_definitions.py |
| GET | `/executor-definitions/{executor_id}/versions` | `list_executor_definition_versions` | routes/executor_definitions.py |
| PUT | `/executor-definitions/{executor_id}/draft` | `save_executor_definition_draft` | routes/executor_definitions.py |
| POST | `/executor-definitions/{executor_id}/publish` | `publish_executor_definition` | routes/executor_definitions.py |
| POST | `/executor-definitions/{executor_id}/rollback` | `rollback_executor_definition` | routes/executor_definitions.py |
| POST | `/executor-definitions/{executor_id}/copy` | `copy_executor_definition` | routes/executor_definitions.py |
| DELETE | `/executor-definitions/{executor_id}` | `archive_executor_definition` | routes/executor_definitions.py |
| GET | `/workspaces/{workspace_id}/failed-node-runs` | `list_failed_node_runs` | routes/failed_node_runs.py |
| POST | `/workspaces/{workspace_id}/jobs/rerun-by-failure` | `rerun_jobs_by_failure_category` | routes/failed_node_runs.py |
| GET | `/admin/instance-settings` | `get_instance_settings` | routes/instance_settings.py |
| PUT | `/admin/instance-settings` | `put_instance_settings` | routes/instance_settings.py |
| GET | `/jobs/{job_id}/artifacts/{artifact_name:path}` | `get_artifact` | routes/job_artifacts.py |
| GET | `/jobs/{job_id}/runs/{run_id}/log` | `get_job_run_log` | routes/job_artifacts.py |
| POST | `/workspaces/{workspace_id}/job-batches` | `create_workspace_job_batch` | routes/job_batches.py |
| GET | `/jobs/{job_id}/{invalid_path:path}` | `reject_invalid_job_subpath` | routes/job_invalid_paths.py |
| GET | `/workspaces/{workspace_id}/jobs/snapshot` | `snapshot_workspace_jobs` | routes/job_list.py |
| GET | `/workspaces/{workspace_id}/jobs/facets` | `workspace_job_facets` | routes/job_list.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-rerun/preview` | `preview_batch_rerun_workspace_jobs` | routes/job_rerun_preview.py |
| POST | `/workspaces/{workspace_id}/events/stress` | `record_stress_events` | routes/job_stress_events.py |
| POST | `/jobs/{job_id}/upgrade-workflow` | `upgrade_job_workflow` | routes/job_workflow_upgrade.py |
| GET | `/workspaces/{workspace_id}/jobs` | `list_workspace_jobs` | routes/jobs.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-rerun` | `batch_rerun_workspace_jobs` | routes/jobs.py |
| DELETE | `/workspaces/{workspace_id}/jobs/batch` | `batch_delete_workspace_jobs` | routes/jobs.py |
| GET | `/jobs/{job_id}` | `get_job` | routes/jobs.py |
| POST | `/jobs/{job_id}/nodes/{node_key}/rerun` | `rerun_node` | routes/jobs.py |
| DELETE | `/jobs/{job_id}` | `delete_job` | routes/jobs.py |
| POST | `/jobs/{job_id}/run-to` | `run_to` | routes/jobs.py |
| POST | `/jobs/{job_id}/continue` | `continue_job` | routes/jobs.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-run-to` | `batch_run_to` | routes/jobs.py |
| GET | `/metrics/overview` | `get_metrics_overview` | routes/metrics.py |
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
| GET | `/workspaces/{workspace_id}/questions/{question_id}` | `get_question_detail` | routes/questions.py |
| GET | `/executors/skills/{skill_key:path}` | `get_skill` | routes/skill_catalog_route.py |
| GET | `/admin/skill-sources` | `get_skill_sources` | routes/skill_sources.py |
| PUT | `/admin/skill-sources/{skill_key:path}` | `put_skill_source` | routes/skill_sources.py |
| POST | `/admin/skill-sources/relock` | `relock_skill_sources` | routes/skill_sources.py |
| POST | `/skills/validate` | `validate_skill` | routes/skills.py |
| GET | `/skills/tags` | `list_skill_tags` | routes/skills.py |
| GET | `/jobs/{job_id}/runs/{run_id}/token-usage` | `get_run_token_usage` | routes/token_usage.py |
| GET | `/jobs/{job_id}/token-usage` | `get_job_token_usage` | routes/token_usage.py |
| GET | `/workspaces/{workspace_id}/token-usage` | `get_workspace_token_usage` | routes/token_usage.py |
| GET | `/admin/token-usage-pricing` | `get_token_usage_pricing` | routes/token_usage_pricing.py |
| PUT | `/admin/token-usage-pricing` | `put_token_usage_pricing` | routes/token_usage_pricing.py |
| GET | `/jobs/{job_id}/video` | `get_video_job_detail` | routes/video_jobs_detail.py |
| GET | `/jobs/{job_id}/video/source` | `get_video_job_source` | routes/video_jobs_source.py |
| GET | `/worker/status` | `worker_status` | routes/worker.py |
| POST | `/worker/pause` | `pause_worker` | routes/worker.py |
| POST | `/worker/resume` | `resume_worker` | routes/worker.py |
| GET | `/workflows` | `list_workflows` | routes/workflow_catalog.py |
| GET | `/workflows/{workflow_key}` | `get_workflow` | routes/workflow_catalog.py |
| POST | `/workspaces/{workspace_id}/workflow-drafts/compare` | `compare_workflow_draft_route` | routes/workflow_draft_compare.py |
| GET | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code` | `get_node_code` | routes/workflow_node_codes.py |
| PUT | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code` | `save_node_code_draft` | routes/workflow_node_codes.py |
| POST | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/publish` | `publish_node_code` | routes/workflow_node_codes.py |
| GET | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/versions` | `list_node_code_versions` | routes/workflow_node_codes.py |
| GET | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/versions/{version}` | `get_node_code_version` | routes/workflow_node_codes.py |
| POST | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/rollback` | `rollback_node_code` | routes/workflow_node_codes.py |
| DELETE | `/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code` | `archive_node_code` | routes/workflow_node_codes.py |
| GET | `/workflow-nodes/files/{file_path:path}` | `read_workflow_node_file` | routes/workflow_node_files.py |
| GET | `/workspaces/{workspace_id}/workflow-revisions` | `list_workflow_revisions` | routes/workflow_revisions.py |
| GET | `/workspaces/{workspace_id}/workflow-revisions/active` | `get_active_workflow_revision` | routes/workflow_revisions.py |
| GET | `/workspaces/{workspace_id}/workflow-revisions/{revision_id}` | `get_workflow_revision_detail` | routes/workflow_revisions.py |
| POST | `/workspaces/{workspace_id}/workflow-drafts/validate` | `validate_workflow_draft` | routes/workflow_revisions.py |
| POST | `/workspaces/{workspace_id}/workflow-drafts/publish` | `publish_draft` | routes/workflow_revisions.py |
| GET | `/workspaces/{workspace_id}/agent-routes` | `get_workspace_agent_routes` | routes/workspace_agent_routes.py |
| PUT | `/workspaces/{workspace_id}/configuration` | `replace_workspace_configuration` | routes/workspace_configuration.py |
| GET | `/executors` | `get_executors` | routes/workspace_executors.py |
| GET | `/workspaces/{workspace_id}/executor-configuration` | `get_workspace_executor_configuration` | routes/workspace_executors.py |
| GET | `/workspaces/{workspace_id}/runs` | `list_workspace_runs` | routes/workspace_runs.py |
| GET | `/workspaces/{workspace_id}/dag` | `get_workspace_dag` | routes/workspace_runs.py |
| GET | `/workspaces/{workspace_id}/secrets` | `list_workspace_secrets` | routes/workspace_secrets.py |
| PUT | `/workspaces/{workspace_id}/secrets/{name}` | `put_workspace_secret` | routes/workspace_secrets.py |
| DELETE | `/workspaces/{workspace_id}/secrets/{name}` | `delete_workspace_secret` | routes/workspace_secrets.py |
| GET | `/workspaces/{workspace_id}/settings` | `get_workspace_settings` | routes/workspace_settings.py |
| PATCH | `/workspaces/{workspace_id}/settings/{section}` | `update_workspace_settings_section` | routes/workspace_settings.py |
| POST | `/workspaces/{workspace_id}/settings/test-connection` | `test_workspace_connection` | routes/workspace_settings.py |
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
| AgentEnqueueConfig | BaseModel | workers: int, max_pending: int | app/agent_broker/dispatch_pool.py |
| AgentDefinition | BaseModel | capability: str, runtime: Literal['pi', 'openclaw', 'velites'], skill: str, t... | app/agent_catalog.py |
| CodeCapabilityConfig | BaseModel | path: str, timeout_seconds: int, sandbox_network: bool, config_schema: dict[s... | app/executors/code_config.py |
| CodeExecutorConfig | BaseModel | kind: Literal['code'], global_capacity: int, capabilities: dict[str, CodeCapa... | app/executors/code_config.py |
| PiCapabilityConfig | BaseModel | skill: str, tools: tuple[str, ...] | app/executors/config.py |
| OpenClawCapabilityConfig | BaseModel | skill: str | app/executors/config.py |
| PiExecutorConfig | BaseModel | kind: Literal['pi'], global_capacity: int, capabilities: dict[str, PiCapabili... | app/executors/config.py |
| OpenClawExecutorConfig | BaseModel | kind: Literal['openclaw'], agent_id: str, global_capacity: int, capabilities:... | app/executors/config.py |
| PiRuntimeConfig | BaseModel | flavor: Literal['pi', 'velites'], binary: str, provider: str, model: str, thi... | app/executors/runtime_config.py |
| OpenClawSkillSafetyRepo | BaseModel | path: str | app/executors/runtime_config.py |
| OpenClawSkillSafetyRuntimeConfig | BaseModel | enabled: bool, repos: list[OpenClawSkillSafetyRepo] | app/executors/runtime_config.py |
| OpenClawRuntimeConfig | BaseModel | command_template: tuple[str, ...], cwd: str, timeout_seconds: int, cancellati... | app/executors/runtime_config.py |
| WorkflowsRuntimeConfig | BaseModel | enabled: bool, custom_nodes_enabled: bool, pi: PiRuntimeConfig | app/executors/runtime_config.py |
| AgentWorkersRuntimeConfig | BaseModel | register_token: str, register_token_file: str, max_archive_bytes: int, min_pr... | app/executors/runtime_config.py |
| ExecutorRuntimeConfig | BaseModel | heartbeat_interval_seconds: float, lease_ttl_seconds: int, heartbeat_failure_... | app/executors/runtime_config.py |
| AgentDefinitionResponse | BaseModel | id: str, runtime: Literal['pi', 'openclaw', 'velites'], capability: str, skil... | app/routes/agent_catalog_contracts.py |
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
| RegisterAgentWorkerResponse | BaseModel | worker_token: str, allowed_workspaces: list[str] | app/routes/agent_workers_contracts.py |
| CreateAgentRegisterTokenRequest | BaseModel | workspace_id: str | None, label: str | app/routes/agent_workers_contracts.py |
| AgentRegisterTokenCreatedResponse | BaseModel | token_id: str, register_token: str, workspace_id: str | None, label: str | app/routes/agent_workers_contracts.py |
| AgentRegisterTokenSummary | BaseModel | token_id: str, workspace_id: str | None, label: str, created_at: str, revoked... | app/routes/agent_workers_contracts.py |
| AgentRegisterTokensResponse | BaseModel | tokens: list[AgentRegisterTokenSummary] | app/routes/agent_workers_contracts.py |
| AgentRegisterTokenRevokeResponse | BaseModel | revoked: bool | app/routes/agent_workers_contracts.py |
| ClaimAgentExecutionRequest | BaseModel | worker_id: str, max_concurrency: int | None | app/routes/agent_workers_contracts.py |
| AgentWorkerSummary | BaseModel | worker_id: str, name: str, runtimes: list[str], capabilities: list[str], mode... | app/routes/agent_workers_contracts.py |
| AgentWorkersResponse | BaseModel | workers: list[AgentWorkerSummary] | app/routes/agent_workers_contracts.py |
| AgentWorkerRevokeResponse | BaseModel | worker_id: str, revoked: bool | app/routes/agent_workers_contracts.py |
| AgentClaimResponse | BaseModel | execution_id: str, lease_id: str, workspace_id: str, job_id: str, workflow_ke... | app/routes/agent_workers_contracts.py |
| AgentStatusResponse | BaseModel | id: str, name: str, busy: bool, current_video_id: str | None, current_title: ... | app/routes/agents.py |
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
| HealthResponse | BaseModel | ok: bool, workers: dict[str, str] | None | app/routes/common.py |
| ExecutorCapabilityResponse | BaseModel | name: str, path: str | None, timeout_seconds: int | None, skill: str | None, ... | app/routes/executor_catalog_contracts.py |
| ExecutorDefinitionResponse | BaseModel | id: str, kind: Literal['code', 'pi', 'openclaw'], global_capacity: int, capab... | app/routes/executor_catalog_contracts.py |
| ExecutorCatalogResponse | BaseModel | executors: list[ExecutorDefinitionResponse], agents: list[AgentDefinitionResp... | app/routes/executor_catalog_contracts.py |
| ExecutorAllocationRequest | BaseModel | executor_id: str, concurrency_limit: int | app/routes/executor_contracts.py |
| NodeBindingRequest | BaseModel | workflow_key: str, node_key: str, executor_id: str | app/routes/executor_contracts.py |
| NodeLimitRequest | BaseModel | workflow_key: str, node_key: str, concurrency_limit: int | app/routes/executor_contracts.py |
| WorkspaceExecutorConfigurationResponse | BaseModel | allocations: list[ExecutorAllocationResponse], bindings: list[NodeBindingRequ... | app/routes/executor_contracts.py |
| WorkspaceAgentRouteEntry | BaseModel | workflow_key: str, node_key: str, node_label: str, capability: str, agent_id:... | app/routes/executor_contracts.py |
| WorkspaceAgentRoutesResponse | BaseModel | routes: list[WorkspaceAgentRouteEntry] | app/routes/executor_contracts.py |
| WorkspaceSettingsPayload | BaseModel | entityType: str, intakeModes: list[str], labelOverrides: dict[str, str], work... | app/routes/executor_contracts.py |
| WorkspaceConfigurationSettingsRequest | BaseModel | entityType: str | None, intakeModes: list[str] | None, labelOverrides: dict[s... | app/routes/executor_contracts.py |
| WorkspaceConfigurationRequest | BaseModel | name: str | None, description: str | None, settings: WorkspaceConfigurationSe... | app/routes/executor_contracts.py |
| WorkspaceConfigurationResponse | BaseModel | workspace: WorkspaceRecord, settings: WorkspaceSettingsPayload, executor_conf... | app/routes/executor_contracts.py |
| ExecutorDefinitionPayload | BaseModel | kind: str, global_capacity: int, capabilities: dict[str, dict[str, Any]] | app/routes/executor_definition_contracts.py |
| ExecutorCopyRequest | BaseModel | new_executor_id: str | app/routes/executor_definition_contracts.py |
| ExecutorRollbackRequest | BaseModel | version: int | app/routes/executor_definition_contracts.py |
| ExecutorVersionResponse | BaseModel | id: str, executor_id: str, version: int, status: Literal['draft', 'published'... | app/routes/executor_definition_contracts.py |
| ExecutorVersionSummary | BaseModel | id: str, executor_id: str, version: int, status: Literal['draft', 'published'... | app/routes/executor_definition_contracts.py |
| ExecutorListItem | BaseModel | executor_id: str, kind: str, global_capacity: int, capabilities: list[str], v... | app/routes/executor_definition_contracts.py |
| ExecutorListResponse | BaseModel | executors: list[ExecutorListItem] | app/routes/executor_definition_contracts.py |
| ExecutorDetailResponse | BaseModel | executor_id: str, latest: ExecutorVersionResponse | None, published: Executor... | app/routes/executor_definition_contracts.py |
| ExecutorVersionsResponse | BaseModel | versions: list[ExecutorVersionSummary] | app/routes/executor_definition_contracts.py |
| ExecutorArchiveResponse | BaseModel | archived: int | app/routes/executor_definition_contracts.py |
| FailedNodeRunItem | BaseModel | job_id: str, node_key: str, node_run_id: int, workflow_key: str, failure_cate... | app/routes/failed_node_run_contracts.py |
| FailedNodeRunsResponse | BaseModel | runs: list[FailedNodeRunItem] | app/routes/failed_node_run_contracts.py |
| InstanceOpenClawSkillSafetyRepo | BaseModel | path: str | app/routes/instance_openclaw_contracts.py |
| InstanceOpenClawSkillSafetySettings | BaseModel | enabled: bool, repos: list[InstanceOpenClawSkillSafetyRepo] | app/routes/instance_openclaw_contracts.py |
| InstanceOpenClawSettings | BaseModel | cwd: str, timeout_seconds: int, isolated_workspace_root: str, command_templat... | app/routes/instance_openclaw_contracts.py |
| InstanceCleanupSettings | BaseModel | log_retention_days: int, run_dir_retention_days: int, interval_seconds: int | app/routes/instance_settings_contracts.py |
| InstanceMonitoringSettings | BaseModel | sample_interval_seconds: float, retention_days: int | app/routes/instance_settings_contracts.py |
| InstanceWorkflowsSettings | BaseModel | enabled: bool | app/routes/instance_settings_contracts.py |
| InstanceAgentWorkersSettings | BaseModel | max_archive_bytes: int, min_protocol_version: int | app/routes/instance_settings_contracts.py |
| InstanceSettingsDocument | BaseModel | cleanup: InstanceCleanupSettings, monitoring: InstanceMonitoringSettings, hea... | app/routes/instance_settings_contracts.py |
| JobFilterPayload | BaseModel | status: str | None, search: str | None, workflow_version: int | None, workflo... | app/routes/job_batch_filter_contracts.py |
| JobSelectionMixin | BaseModel | job_ids: list[str] | None, filter: JobFilterPayload | None, exclude_ids: list... | app/routes/job_batch_filter_contracts.py |
| JobBatchRequest | BaseModel | workflow_key: str, entity: str | None, source_kind: str, question_ids: list[s... | app/routes/job_contracts.py |
| JobBatchResponse | BaseModel | batch: dict[str, Any], created_count: int, jobs: list[dict[str, Any]] | app/routes/job_contracts.py |
| WorkspaceCreateRequest | BaseModel | name: str, default_workflow_key: str, default_entity: str, resource_config: d... | app/routes/job_contracts.py |
| WorkspaceUpdateRequest | BaseModel | name: str | None, description: str | None, default_workflow_key: str | None, ... | app/routes/job_contracts.py |
| WorkspaceSettingsResponse | BaseModel | settings: dict[str, Any] | app/routes/job_contracts.py |
| WorkspaceSettingsSectionRequest | BaseModel | entityType: str | None, intakeModes: list[str] | None, labelOverrides: dict[s... | app/routes/job_contracts.py |
| WorkspaceSettingsTestResponse | BaseModel | ok: bool, message: str | app/routes/job_contracts.py |
| WorkspaceResponse | BaseModel | workspace: WorkspaceRecord | app/routes/job_contracts.py |
| WorkspacesResponse | BaseModel | workspaces: list[WorkspaceRecord] | app/routes/job_contracts.py |
| DeleteJobResponse | BaseModel | deleted: str | app/routes/job_contracts.py |
| ArtifactResponse | BaseModel | name: str, content: str | app/routes/job_contracts.py |
| WorkspaceRunsResponse | BaseModel | runs: list[dict[str, Any]] | app/routes/job_contracts.py |
| WorkspaceDagResponse | BaseModel | workflow: dict[str, Any], nodes: list[dict[str, Any]] | app/routes/job_contracts.py |
| ExecutorRuntimeStatus | BaseModel | executor_id: str, kind: str, global_capacity: int, workspace_limit: int, runn... | app/routes/job_contracts.py |
| ExecutorStatusSummary | BaseModel | executors: list[ExecutorRuntimeStatus] | app/routes/job_contracts.py |
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
| QuestionNormalized | BaseModel | stem: str | None, options: list[dict[str, Any]] | None, answer: Any | None, a... | app/routes/questions.py |
| QuestionDetailResponse | BaseModel | question_id: str, title: str, normalized: QuestionNormalized, cms_payload: di... | app/routes/questions.py |
| SkillFileResponse | BaseModel | path: str, size: int, content: str, truncated: bool | app/routes/skill_contracts.py |
| SkillDetailResponse | BaseModel | key: str, ref: str, commit: str, available: bool, files: list[SkillFileRespon... | app/routes/skill_contracts.py |
| SkillValidateRequest | BaseModel | path: str | app/routes/skill_contracts.py |
| SkillValidateResponse | BaseModel | valid: bool, path: str, skill_key: str | None, error: str | None, tags: list[... | app/routes/skill_contracts.py |
| SkillTagsResponse | BaseModel | path: str, tags: list[str], latest_tag: str | None | app/routes/skill_contracts.py |
| SkillSourceEntry | BaseModel | key: str, repo: str, ref: str, locked_commit: str | None, resolved_at: str | ... | app/routes/skill_source_contracts.py |
| SkillSourcesResponse | BaseModel | skills: list[SkillSourceEntry] | app/routes/skill_source_contracts.py |
| SkillSourceUpdate | BaseModel | repo: str, ref: str | app/routes/skill_source_contracts.py |
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
| WorkflowsListResponse | BaseModel | workflows: list[WorkflowSummaryResponse] | app/routes/workflow_contracts.py |
| WorkflowDraftCompareRequest | BaseModel | definition_yaml: str | app/routes/workflow_draft_compare_contracts.py |
| WorkflowDraftCompareError | BaseModel | category: str, message: str, line: int | None, column: int | None, node_key: ... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowRevisionSummaryItem | BaseModel | id: str, version: int, workflow_key: str, definition_hash: str | app/routes/workflow_draft_compare_contracts.py |
| WorkflowDraftSummaryItem | BaseModel | key: str, label: str, version: int | app/routes/workflow_draft_compare_contracts.py |
| WorkflowNodeChange | BaseModel | type: WorkflowChangeType, node_key: str, label: str, fields: list[str], risk:... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowEdgeChange | BaseModel | type: WorkflowEdgeChangeType, source: str, target: str, before_condition: str... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowIntakeChange | BaseModel | type: WorkflowIntakeChangeType, mode_key: str, field_key: str | None, risk: W... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowRiskFlag | BaseModel | code: str, severity: WorkflowRiskLevel, message: str | app/routes/workflow_draft_compare_contracts.py |
| WorkflowCompareSummary | BaseModel | risk_level: WorkflowRiskLevel, node_changes: list[WorkflowNodeChange], edge_c... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowDraftCompareResponse | BaseModel | valid: bool, creates_revision: bool, base_revision: WorkflowRevisionSummaryIt... | app/routes/workflow_draft_compare_contracts.py |
| WorkflowMetadataChange | BaseModel | type: Literal['modified'], field: str, before_value: str | None, after_value:... | app/routes/workflow_draft_compare_metadata_contracts.py |
| WorkflowNodeCodeResponse | BaseModel | origin: Literal['builtin', 'custom'], code: str, path: str | None, version: i... | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeDraftRequest | BaseModel | code: str, change_note: str | None | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeVersionResponse | BaseModel | id: str, version: int, status: str, code: str, code_hash: str, created_by: st... | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeVersionSummary | BaseModel | id: str, version: int, status: str, code_hash: str, created_by: str, change_n... | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeVersionsResponse | BaseModel | versions: list[WorkflowNodeCodeVersionSummary] | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeRollbackRequest | BaseModel | version: int | app/routes/workflow_node_code_contracts.py |
| WorkflowNodeCodeArchiveResponse | BaseModel | archived: int | app/routes/workflow_node_code_contracts.py |
| WorkflowTerminalResponse | BaseModel | outcome: str | app/routes/workflow_node_contracts.py |
| WorkflowNodeExecutionResponse | BaseModel | provider: str, model: str, thinking: str, prompt: str | app/routes/workflow_node_contracts.py |
| WorkflowNodeResponse | BaseModel | key: str, label: str, capability: str, after: list[str], inputs: list[str], o... | app/routes/workflow_node_contracts.py |
| WorkflowNodeCapabilityReference | BaseModel | executor_id: str, capability: str | app/routes/workflow_node_file_contracts.py |
| WorkflowNodeFileResponse | BaseModel | path: str, content: str, capabilities: list[WorkflowNodeCapabilityReference] | app/routes/workflow_node_file_contracts.py |
| WorkflowRevisionSummary | BaseModel | id: str, workspace_id: str, workflow_key: str, version: int, status: str, def... | app/routes/workflow_revisions_contracts.py |
| WorkflowRevisionsResponse | BaseModel | revisions: list[WorkflowRevisionSummary] | app/routes/workflow_revisions_contracts.py |
| WorkflowDraftRequest | BaseModel | definition_yaml: str | app/routes/workflow_revisions_contracts.py |
| WorkflowDraftValidationResponse | BaseModel | valid: bool, errors: list[str] | app/routes/workflow_revisions_contracts.py |
| ActiveWorkflowRevisionResponse | BaseModel | revision: WorkflowRevisionSummary, workflow: workflow_contracts.WorkflowDefin... | app/routes/workflow_revisions_contracts.py |
| WorkflowRevisionDetailResponse | BaseModel | revision: WorkflowRevisionSummary, workflow: workflow_contracts.WorkflowDefin... | app/routes/workflow_revisions_contracts.py |
| WorkspaceRecord | BaseModel | id: str, name: str, description: str, default_workflow_key: str, default_enti... | app/routes/workspace_contracts.py |
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
| VideoJobInputResponse | BaseModel | schema_version: int, entity_type: str, content_type: str, legacy_video_id: st... | app/video_capabilities/response_contracts.py |
| VideoSubtitleResponse | BaseModel | index: int, start: float, end: float, text: str | app/video_capabilities/response_contracts.py |
| VideoJobArtifactsResponse | BaseModel | subtitles: list[VideoSubtitleResponse], chapters: list[dict[str, Any]], inter... | app/video_capabilities/response_contracts.py |
| VideoJobDetailResponse | BaseModel | input: VideoJobInputResponse, artifacts: VideoJobArtifactsResponse | app/video_capabilities/response_contracts.py |
| AgentStockConfig | BaseModel | enabled: bool, window_seconds: int, horizon_seconds: int, min_stock: int, max... | app/workflow_worker/agent_stock.py |

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

  ratchet 脚本不会提高 ceiling；超出预算的文件必须拆分或回退。ceiling 按有效行数计
  （排除注释行与空行，实现见 `scripts/architecture/effective_lines.py`），压缩注释
  对预算没有帮助。此外 production 文件有
  800 行绝对上限（`production.max_lines`，按原始行数计），豁免也不能突破；挂账超过 30 天的豁免由
  `scripts/check_exemption_age.py` 在 full gate 中告警（不阻断）。

## Runtime Architecture

### 后端

- `server.app.main:create_app(data_dir, start_worker)` 是 FastAPI 应用工厂。
- 当 `start_worker=True` 时，生命周期内启动 `WorkflowWorkerThread`：
  - 在 DB 实例设置 `workflows.enabled` 为 `true` 时轮询 Agent Legion DAG 任务。
  - 视频 Job 由 `video_knowledge` workflow 的节点（`download_video`（code executor）、`transcribe_video`、Agent 阶段、`assemble_video_metadata`、`package_video_job`）处理。
- worker 默认处于**暂停**状态；调用 `POST /api/worker/resume` 开始处理。
- 视频 Job 的 `content_type` 固定为 `knowledge`（`video_capabilities/contracts.py` 强制校验），pipeline 节点序列：

  **Knowledge videos (`knowledge`):**
  1. `download_video` — 下载 MP4（`code` executor，代码在 `workflow_nodes/video_download.py`）；`batch_by_knowledge` 模式下先经节点 config + vault 把 `knowledge_code` 解析为播放地址（见下文 Job Intake 资源解析）
  2. `transcribe_video` — 生成 `subtitles.srt` 与 `transcription.json`
  3. `subtitle_review` — openclaw agent
  4. `chapter_generate` — openclaw agent
  5. `interaction_generate` — openclaw agent
  6. `content_review` — openclaw agent
  7. `assemble_video_metadata` — 生成 `metadata.json`、`report.md`
  8. `package_video_job` — 创建 ZIP package

- direct URL intake 会校验 URL，非法即拒绝；knowledge 模式空 URL 在 `download_video` 节点执行期报错失败（`server/app/workflows/video_knowledge_source.py`）。
- 任一 node 失败会把 Job 置为 `failed`，错误写入数据库与日志文件。
- 支持从任意 node 重跑；重跑会清除该 node 及下游所有 artifacts。
- `DELETE /api/jobs/{job_id}` 会级联删除 Job 记录、`node_runs`、本地 Job 目录与日志。

### Job Intake 资源解析（resolve phase）

Intake 模式的 CMS 解析时机由 `server/app/services/job_intake_registry.py` 的 `RESOLVERS` 声明式注册表决定，每个 `(entity, mode)` 对应一个 `ResolverSpec`（`phase` / `resource_key` / `handler`）：

- `phase="node"`：intake 只做无外部调用的 fan-out，candidate 只携带 opaque `source_ref`（question 为题目 id / 知识点 code，video 为知识点 code）；解析下沉到首节点执行期，经节点 config（capability `config_schema` 出厂默认值 ← 节点/workspace 覆盖，叠加 settings 层 env 注入的 `cms` 键）+ vault 完成。两个 workflow 的首节点都是 `code` executor 节点：`question_comprehension_info.fetch_questions`（`workflow_nodes/question_intake.py`，按冻结 payload 的 `intake_mode.input_field` 兼容 by-id 与 by-knowledge 输入）与 `video_knowledge.download_video`（`workflow_nodes/video_download.py`，`knowledge_code → 播放地址` 解析并回写 `video_input.json`）。
- `phase=None`：direct 模式，不访问外部资源。

`phase="intake"`（intake 期调 CMS 做 1:N fan-out）已从 question resolver 退役：intake 不再调用 CMS，非法 id/code 在执行期以 job 失败暴露。

接入新内容类型只需两步：在 `RESOLVERS` 注册 resolver、为 DAG 首节点绑定 capability 并在其 `config_schema` 声明 CMS 连接键（`base_url` / `api_url` / `token`（`secret: true`）等）。Intake 快照只冻结 `node_config` 与 `secret_ref`。

## Database

- PostgreSQL 同时服务视频 pipeline 与 Agent Legion workflow（当前 `SCHEMA_VERSION = 24`）：
  - `workspaces` — Agent Legion workspace 定义（含 `default_workflow_key`, `node_config_json`, `default_entity`, `intake_config_json`）。`node_config_json` 里 schema 标记 `secret: true` 的字段只存 `{"secret_ref": "<name>"}` 引用，明文不落库（见下文 Secrets Vault）；旧 `resource_config_json`（resource binding）已在 v24 迁移为节点覆盖并清空
  - `workspace_secrets` — vault 加密存储的 workspace 密钥（Fernet 密文，`(workspace_id, name)` 唯一，v16 新增）
  - `job_batches`, `jobs`, `job_nodes`, `node_runs` — DAG job 相关表
  - `workflow_revisions` — workflow 版本修订历史
  - `workspace_packages` — 已创建 package 路径
- 初始化器在 PostgreSQL advisory lock 下按版本应用 schema。
- `JobQueries.connect()` 是上下文管理器（定义在 `JobQueriesBase`），确保 `conn.close()`；workspace 侧查询由 `WorkspaceQueriesMixin` 合并进统一的 `JobQueries`。
- `JobDeletionService` 级联删除 Job 记录、`node_runs`、本地 Job 目录与日志。
- 存储路径以**相对 POSIX 路径**保存在 `settings.data_dir` 下（前缀为 `videos/`, `jobs/`, `logs/`, `packages/`），API 返回时投影为绝对路径。
- SQL 占位符约定：**新 SQL 一律写 psycopg 的 `%s`**，不要再写 SQLite 风格的 `?`。存量 `?` 由 `server/app/db/dialect.py` 盲替换为 `%s`，该层无法区分占位符与 Postgres JSON 的 `?`/`?|`/`?&` 操作符；`scripts/check_architecture.py` 的 SQL 占位符检查（基线 `config/architecture/sql-placeholders-baseline.json`）按 ratchet 方式只降不升，新文件出现任何 SQL `?` 即失败，改写存量后同步下调基线。

## New Subsystems

### Workflow Studio & Workflow Revisions

Workflow Studio 提供可视化 workflow 编辑能力，与版本修订历史集成。

- **Routes**: `routes/workflow_revisions.py`, `routes/workflow_draft_compare.py`
- **Services**: `services/workflow_drafts.py`, `services/workflow_draft_publish.py`, `services/workflow_revision_format.py`, `services/job_workflow_versions.py`, `services/job_workflow_upgrade.py`; `/api/executors` 同时返回已发布 Agent Catalog 投影（versioned_entities），供编辑器按 capability 获取 runtime、skill、tools；provider/model/thinking 的「继承默认」提示改读 workspace settings 的 agentDefaults
- **DB**: PostgreSQL `workflow_revisions` 表与版本化 schema 初始化
- **Frontend**: `pages/WorkflowStudioPage.tsx`, `pages/workflowStudio/`

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

### Video Capabilities

`server/app/video_capabilities/` 为视频 Job 提供统一的输入/产物合约与响应投影。

- `contracts.py`, `response_contracts.py`: 视频详情与产物响应模型。
- `projection.py`: 将底层 artifacts 投影为 API 响应。

### Secrets Vault

`server/app/services/vault.py` 提供按 workspace 隔离的凭证保管库（VAULT-SECRET-001）。

- **Routes**: `routes/workspace_secrets.py`（`GET/PUT/DELETE /workspaces/{workspace_id}/secrets[/{name}]`），write-only：GET 只返回 name 与时间戳元数据，任何响应都不含明文或密文。
- **Service**: `VaultService`（Fernet 加解密 + `workspace_secrets` 持久化，明文不跨越服务层边界落盘或出 API）与 `WorkspaceSecretsService`（API 门面）。
- **Master key**: env `AGENT_LEGION_VAULT_MASTER_KEY` / `AGENT_LEGION_VAULT_MASTER_KEY_FILE`（映射到 `vault.master_key` / `vault.master_key_file`）；缺 key 时 server 可启动，但 vault 写操作与 `secret_ref` 解析报错。
- **写入链**: 节点配置保存时，capability `config_schema` 标记 `secret: true` 的字段值转存 vault，节点覆盖只留 `{"secret_ref": "node:{workflow_key}:{node_key}:{field}"}`；settings payload 中 secret 字段只返回 `{"secret_set": bool}`。
- **运行时解析**: `resolve_secret_refs` 在 server 端把 `secret_ref` 解析为明文（仅内存；字符串明文透传为兼容窗口），消费点为 dispatch 执行注入、question detail 与 settings test-connection 三处；intake 冻结的是 `secret_ref` 而非明文；`job_logs` 脱敏并入 vault 明文。test-connection 还会用解析出的 token 真实探测 CMS（连通性 + 401/403 鉴权判定），响应只报告来源（workspace node config / 全局 env），不回显 token。

## Configuration Reference

运行时配置已全部从 split yaml 退役：`config/app.yaml` / `config/workflow.yaml` / `config/agent_legion.yaml` 存在即启动报错（带迁移指引，见 `server/app/configuration/owned_keys.py`）。有效配置 = 代码默认值 + env 覆盖 + DB 文档。

`config/app.yaml` 已整体退役：bootstrap/安全类键转 env-only，实例级可调配置迁入 DB：

- env-only：`database.url` → `AGENT_LEGION_DATABASE_URL`（唯一权威变量，G4；缺省 `postgresql://127.0.0.1:5432/agent_legion`）；`data_dir` → `AGENT_LEGION_DATA_DIR`（缺省 `data`）；`server.cors` → `AGENT_LEGION_CORS_ALLOW_ORIGINS`（逗号分隔）/ `AGENT_LEGION_CORS_ALLOW_CREDENTIALS`；`agent_workers.register_token[_file]` → `AGENT_LEGION_WORKER_REGISTER_TOKEN[_FILE]`（缺省读 `deploy/secrets/agent_worker_register_token`）。
- DB 实例设置（`global_settings` 表 `instance` 文档，`GET/PUT /api/admin/instance-settings`，启动 hydration、重启生效，无运行期热更新）：`cleanup.log_retention_days` / `run_dir_retention_days` / `interval_seconds`（日志与运行目录清理策略）、`monitoring.sample_interval_seconds` / `retention_days`（资源监控采样间隔与保留天数）、`heartbeat_interval_seconds` / `lease_ttl_seconds` / `heartbeat_failure_threshold` / `sweeper_enabled` / `sweeper_interval_seconds`、`workflows.enabled`（是否启用 Agent Legion DAG workflow worker）、`agent_workers.max_archive_bytes` / `min_protocol_version`、`openclaw.cwd` / `timeout_seconds` / `isolated_workspace_root` / `command_template`（含 `{prompt_text}`、`{video_id}`、`{timestamp}` 占位符的命令参数列表）/ `skill_safety`（OpenClaw skill 安全校验；`repos` 只声明允许强制恢复的 path 白名单，恢复 ref 统一从 DB `skill_lock` 文档（`global_settings`）解析——config 治理 G3 单源化，实例设置 API 写 `ref` 返回 422，explicit 单文件配置写 `ref` 启动即报错）。代码默认值 = 退役前 tracked yaml 的生效值；`AGENT_LEGION_OPENCLAW_CWD` 作为 env 覆盖优先级高于 DB 文档。

env-only 段：`vault`（master key）与 `auth`（bootstrap admin 密码）不属于任何 split 文件的 owned keys，只能经环境变量注入（`AGENT_LEGION_VAULT_MASTER_KEY[_FILE]`、`AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD`）；写进 yaml 会触发 owned-key 校验报错。数据库 URL 同样由 env 治理：`AGENT_LEGION_DATABASE_URL` 为唯一权威变量（G4）。

`config/agent_legion.yaml` 的 `asr:` 段已退役（文件整体存在即报错）：业务参数 `provider`（`auto` / `whisper` / `sensevoice`，默认 `auto`）与 `timeout_seconds`（默认 900）声明在 `transcribe_video` capability 的 `config_schema`，沿「schema defaults → 节点 config → workspace 覆盖」链解析（Studio 节点配置可改）；机器路径转 env-only：`AGENT_LEGION_ASR_WHISPER_BINARY` / `AGENT_LEGION_ASR_WHISPER_MODEL` / `AGENT_LEGION_ASR_WHISPER_VAD_MODEL`（可选 VAD 模型）/ `AGENT_LEGION_ASR_SENSEVOICE_SCRIPT` / `AGENT_LEGION_ASR_SENSEVOICE_MODEL_DIR`。启动预检只在 env 显式注入时校验所给路径存在（配错即 fail-fast）；未配置时 server 正常启动，缺二进制在转写时由 provider 的 FileNotFoundError 报错。

CMS 集成不经全局 yaml 段配置（全局 `cms:` 段已退役，写进任何 split yaml 会撞退役文件校验报错）：`env` / `bank_version` / `country_id` / `subject_id` / `page_size` 的出厂默认值声明在 `fetch_questions` / `download_video` capability 的 `config_schema`（内置 executor 工厂定义，DB `versioned_entities` 承载），沿「schema defaults → 节点 config → workspace 覆盖」链解析（Settings UI 可改）；`base_url` 无出厂默认值，由节点/workspace 配置或 env `CMS_BASE_URL` 提供。token 只走 env（`AGENT_LEGION_CMS_TOKEN` / `CMS_*`，`BASECMS_*` 为 deprecated alias）或节点配置的 `secret: true` 字段（workspace node config + vault）；单文件 explicit 配置里出现 `cms.token` / `cms.token_gen` 启动即报错（config 治理 G2）。token 调用时优先级（`cms/client.py` `get_token`）：节点 config token（以 `token_from_binding` 标记）> env `CMS_TOKEN` > settings 注入值 > `token_gen`（仅 prod）；无节点 token 时行为与纯 env 部署一致。env 级凭据缺失在启动校验时只记 warning（workspace vault token 启动时无法预检），不再 fail-fast

`config/workflow.yaml` 的 `executors` 段已退役进 DB：executor 定义（code capability 以 `path` 指向 `workflow_nodes/` 下的仓库内 Python 文件（模块级 `run(job, job_dir, runtime)` 契约），另可声明 `config_schema`（与 `AgentDefinition.config_schema` 同一子集），节点可调参数经 node_config 解析链注入节点 runtime 的 `node_config` 键）存于 `versioned_entities` 表，内置工厂目录（`server/app/executors/builtin_definitions.py`）在启动时 seed-if-absent，Studio 管理发布，重启生效。

实例级运行时设置（`agent_workers` 限额、`workflows.enabled`、lease/heartbeat/sweeper 时序）不再出现在 yaml，见上文「DB 实例设置」。

token 用量计价已产品化：定价存于 `global_settings` 表（`token_usage` 文档），由 admin 在「全局设置」页（`GET/PUT /api/admin/token-usage-pricing`）维护，成本按每条 run 的 provider + model 匹配定价逐行计算；不再有任何 yaml 侧配置。

Agent 定义不再经 yaml 配置（`agents:` 段与 `workflows.pi` 块已在 schema v27 退役，出现在 yaml 中启动即报错）：AgentDefinition 存于 `versioned_entities` 表（全局，workspace_id NULL），经 Studio「Agent 管理」或 `/api/agent-definitions` 做 draft → publish → archive 生命周期管理；热读路径经 `AgentService` 的短 TTL（5s）published 缓存。执行配置（provider/model/thinking）不含在 AgentDefinition 内，按严格链解析：节点 `execution.*` 覆盖 → workspace `default_agent_*`（Settings「Agent 默认配置」）→ 报错（无全局兜底）；thinking 可空（空 = runtime 决定）。

其他配置文件：

- 外部 Pi skill 仓库源与固定 commit 已产品化：声明（`{repo, ref}`）与解析后的 commit 锁存 DB `global_settings`（`skill_sources` / `skill_lock` 文档），lock 是 skill ref 的唯一权威（G3）；经 admin API（`GET/PUT /api/admin/skill-sources`、`POST /api/admin/skill-sources/relock`）与 /admin/settings「Skill 源管理」维护，CLI `make skills-lock`（`uv run python -m server.app.skills.lock`）刷新锁。tracked `config/skills.yaml` / `config/skills.lock` 已退役：DB 无记录且文件存在时启动一次性导入并 warning，否则用内置常量（`server/app/skills/builtin_sources.py`）seed，此后文件不再读取。
- 内置 workflow DAG 定义在 `server/app/workflows/builtin.py`（Python 常量，随代码走 git review），Node 只声明 `capability`，不声明 `runner`/`agent`/`skill`；workspace 绑定时发布为 per-workspace DB revision。
- `config/agent-worker.example.yaml`：Worker Service 引导配置样例（`host_url` / `worker_id` / `runtimes` / `capabilities` / `max_concurrency` 等），Worker 侧独立加载，不经 server 的 owned-key 校验。
- `config/architecture/*`：架构不变量、豁免、源文件体积预算。

## Testing

- 测试位于 `tests/`，使用 pytest。
- `pyproject.toml` 配置 `pythonpath = ["."]`，支持 `server.app.db` 这类导入。
- 覆盖率阈值 `fail_under = 85`（`pyproject.toml`）。
- API 测试使用 `fastapi.testclient.TestClient`，`client` fixture 必须 `with TestClient(app) as c:`。
- Worker 测试注入 mock `TranscriptionProvider`，避免依赖真实 ASR 二进制。

常用命令：

```bash
UV_CACHE_DIR=.uv-cache uv run pytest -q
UV_CACHE_DIR=.uv-cache uv run pytest -q --cov=server --cov-report=term-missing
```

## Security Considerations

- 后端通过 `requests` 下载任意 URL；只在可信输入环境下运行。
- OpenClaw 命令通过 `subprocess.Popen(argv, shell=False)` 执行，模板来自 DB 实例设置文档（`/api/admin/instance-settings`，仅管理员可写）；`{prompt_text}` 替换前经 null 字节剔除与 `shlex.quote` 清洗，OpenClaw skill 仓库在每次运行前强制 checkout 回锁定 ref 并剥离 `GIT_*` 环境变量。
- PostgreSQL 与视频存储部署在受信网络内；业务 API 均需登录（cookie session 或 Bearer token，见 README 的 User Authentication 章节），uvicorn 默认绑定 127.0.0.1，启动脚本与 Makefile 均显式固定 `--host 127.0.0.1`。不要用 `--host 0.0.0.0` 把开发服务器暴露到局域网或任何不可信网络——暴露后任何通过鉴权的用户都可删除 job、下载产物、触发执行。
- Workspace 凭证（如 CMS token）经 vault 加密落库（`workspace_secrets`，Fernet），API 永不返回明文，配置与 intake 快照只存 `secret_ref`；master key 走 env / 文件注入，不进 DB、不进日志（VAULT-SECRET-001）。
- `data/` 已加入 `.gitignore`，禁止提交运行时数据或密钥。
