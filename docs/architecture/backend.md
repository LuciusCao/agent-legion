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
│   ├── schema.py           # 表结构定义
│   └── queries/            # 共享查询构造
├── jobs/                   # Job 领域查询与类型
│   └── queries/            # JobQueries、WorkspaceQueries 等
├── cms/                    # CMS 客户端
│   ├── auth.py             # 认证
│   ├── client.py           # HTTP 客户端
│   ├── knowledge.py        # 知识库查询
│   └── question.py         # 题库查询
├── configuration/          # 配置加载与 owned-keys 校验
├── quality/                # 架构不变量与豁免运行时检查
├── video_capabilities/     # 视频能力合约与投影
├── executors/              # Executor 配置、Runtime、租赁调度
├── agents.py               # Agent 发现与状态跟踪
├── workflow_worker_thread.py # DAG workflow worker 线程
├── workflow_worker_ready.py  # 每 pass 一次的 ready 候选收集（批量状态查询）
└── workflow_worker_schedule.py # ready 候选的 lease 认领与提交
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
- CORS 来源由 `config/app.yaml` 的 `server.cors` 显式配置；默认仅允许本机 Vite 开发源。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### REST API 路由

> 所有路由挂载在 `/api` 前缀下。

| 方法 | 路径 | 处理函数 | 文件 |
|------|------|----------|------|
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
| POST | `/agent-executions/{execution_id}/result` | `result` | routes/agent_workers.py |
| GET | `/agents` | `list_agents` | routes/agents.py |
| WEBSOCKET | `/agents` | `agents_ws` | routes/agents.py |
| POST | `/artifacts` | `upload_artifact` | routes/artifacts.py |
| GET | `/artifacts/{hash}` | `download_artifact` | routes/artifacts.py |
| GET | `/health` | `health` | routes/common.py |
| GET | `/dashboard/events` | `dashboard_events` | routes/dashboard_events.py |
| GET | `/workspaces/{workspace_id}/failed-node-runs` | `list_failed_node_runs` | routes/failed_node_runs.py |
| POST | `/workspaces/{workspace_id}/jobs/rerun-by-failure` | `rerun_jobs_by_failure_category` | routes/failed_node_runs.py |
| GET | `/jobs/{job_id}/artifacts/{artifact_name:path}` | `get_artifact` | routes/job_artifacts.py |
| GET | `/jobs/{job_id}/runs/{run_id}/log` | `get_job_run_log` | routes/job_artifacts.py |
| POST | `/workspaces/{workspace_id}/job-batches` | `create_workspace_job_batch` | routes/job_batches.py |
| GET | `/jobs/{job_id}/{invalid_path:path}` | `reject_invalid_job_subpath` | routes/job_invalid_paths.py |
| GET | `/workspaces/{workspace_id}/jobs/snapshot` | `snapshot_workspace_jobs` | routes/job_snapshot.py |
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
| GET | `/workspaces/{workspace_id}/questions/{question_id}` | `get_question_detail` | routes/questions.py |
| GET | `/executors/skills/{skill_key:path}` | `get_skill` | routes/skill_catalog_route.py |
| GET | `/jobs/{job_id}/runs/{run_id}/token-usage` | `get_run_token_usage` | routes/token_usage.py |
| GET | `/jobs/{job_id}/token-usage` | `get_job_token_usage` | routes/token_usage.py |
| GET | `/workspaces/{workspace_id}/token-usage` | `get_workspace_token_usage` | routes/token_usage.py |
| GET | `/jobs/{job_id}/video` | `get_video_job_detail` | routes/video_jobs_detail.py |
| GET | `/jobs/{job_id}/video/source` | `get_video_job_source` | routes/video_jobs_source.py |
| GET | `/worker/status` | `worker_status` | routes/worker.py |
| POST | `/worker/pause` | `pause_worker` | routes/worker.py |
| POST | `/worker/resume` | `resume_worker` | routes/worker.py |
| GET | `/workflows` | `list_workflows` | routes/workflow_catalog.py |
| GET | `/workflows/{workflow_key}` | `get_workflow` | routes/workflow_catalog.py |
| POST | `/workspaces/{workspace_id}/workflow-drafts/compare` | `compare_workflow_draft_route` | routes/workflow_draft_compare.py |
| GET | `/resource-providers` | `get_resource_providers` | routes/workflow_resource_providers.py |
| GET | `/global-services` | `get_global_services` | routes/workflow_resource_providers.py |
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
| AgentDefinition | BaseModel | capability: str, runtime: Literal['pi', 'openclaw'], skill: str, tools: tuple... | app/agent_catalog.py |
| LocalCapabilityConfig | BaseModel | handler: str, config_schema: dict[str, Any] | app/executors/config.py |
| PiCapabilityConfig | BaseModel | skill: str, tools: tuple[str, ...] | app/executors/config.py |
| OpenClawCapabilityConfig | BaseModel | skill: str | app/executors/config.py |
| LocalExecutorConfig | BaseModel | kind: Literal['local'], global_capacity: int, capabilities: dict[str, LocalCa... | app/executors/config.py |
| PiExecutorConfig | BaseModel | kind: Literal['pi'], global_capacity: int, capabilities: dict[str, PiCapabili... | app/executors/config.py |
| OpenClawExecutorConfig | BaseModel | kind: Literal['openclaw'], agent_id: str, global_capacity: int, capabilities:... | app/executors/config.py |
| PiRuntimeConfig | BaseModel | binary: str, provider: str, model: str, thinking: str, timeout_seconds: int, ... | app/executors/runtime_config.py |
| OpenClawSkillSafetyRuntimeConfig | BaseModel | enabled: bool, repos: list[dict[str, str]] | app/executors/runtime_config.py |
| OpenClawRuntimeConfig | BaseModel | command_template: tuple[str, ...], cwd: str, timeout_seconds: int, cancellati... | app/executors/runtime_config.py |
| WorkflowsRuntimeConfig | BaseModel | enabled: bool, pi: PiRuntimeConfig | app/executors/runtime_config.py |
| AgentWorkersRuntimeConfig | BaseModel | register_token: str, register_token_file: str, max_archive_bytes: int, min_pr... | app/executors/runtime_config.py |
| ExecutorRuntimeConfig | BaseModel | heartbeat_interval_seconds: float, lease_ttl_seconds: int, heartbeat_failure_... | app/executors/runtime_config.py |
| AgentDefinitionResponse | BaseModel | id: str, runtime: Literal['pi', 'openclaw'], capability: str, skill: str, too... | app/routes/agent_catalog_contracts.py |
| RegisterAgentWorkerRequest | BaseModel | worker_id: str, name: str, runtimes: list[str], capabilities: list[str], mode... | app/routes/agent_workers.py |
| RegisterAgentWorkerResponse | BaseModel | worker_token: str, allowed_workspaces: list[str] | app/routes/agent_workers.py |
| CreateAgentRegisterTokenRequest | BaseModel | workspace_id: str | None, label: str | app/routes/agent_workers.py |
| AgentRegisterTokenCreatedResponse | BaseModel | token_id: str, register_token: str, workspace_id: str | None, label: str | app/routes/agent_workers.py |
| AgentRegisterTokenSummary | BaseModel | token_id: str, workspace_id: str | None, label: str, created_at: str, revoked... | app/routes/agent_workers.py |
| AgentRegisterTokensResponse | BaseModel | tokens: list[AgentRegisterTokenSummary] | app/routes/agent_workers.py |
| AgentRegisterTokenRevokeResponse | BaseModel | revoked: bool | app/routes/agent_workers.py |
| ClaimAgentExecutionRequest | BaseModel | worker_id: str, max_concurrency: int | None | app/routes/agent_workers.py |
| AgentWorkerSummary | BaseModel | worker_id: str, name: str, runtimes: list[str], capabilities: list[str], mode... | app/routes/agent_workers.py |
| AgentWorkersResponse | BaseModel | workers: list[AgentWorkerSummary] | app/routes/agent_workers.py |
| AgentWorkerRevokeResponse | BaseModel | worker_id: str, revoked: bool | app/routes/agent_workers.py |
| AgentClaimResponse | BaseModel | execution_id: str, lease_id: str, workspace_id: str, job_id: str, workflow_ke... | app/routes/agent_workers.py |
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
| HealthResponse | BaseModel | ok: bool | app/routes/common.py |
| ExecutorCapabilityResponse | BaseModel | name: str, handler: str | None, skill: str | None, tools: list[str], provider... | app/routes/executor_catalog_contracts.py |
| ExecutorDefinitionResponse | BaseModel | id: str, kind: Literal['local', 'pi', 'openclaw'], global_capacity: int, capa... | app/routes/executor_catalog_contracts.py |
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
| FailedNodeRunItem | BaseModel | job_id: str, node_key: str, node_run_id: int, workflow_key: str, failure_cate... | app/routes/failed_node_run_contracts.py |
| FailedNodeRunsResponse | BaseModel | runs: list[FailedNodeRunItem] | app/routes/failed_node_run_contracts.py |
| JobRerunByFailureRequest | BaseModel | category: Literal['technical', 'business', 'unknown'], strategy: Literal['aut... | app/routes/failed_node_run_contracts.py |
| JobRerunByFailureResponse | BaseModel | results: list[JobRerunByFailureResultResponse] | app/routes/failed_node_run_contracts.py |
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
| ResourceProvidersResponse | BaseModel | providers: list[ResourceProviderDefinition] | app/routes/job_contracts.py |
| GlobalServicesResponse | BaseModel | cms: CmsServiceStatus | app/routes/job_contracts.py |
| ExecutionControlSummaryResponse | BaseModel | mode: Literal['full', 'until_node'], target_node_key: str | None, paused: boo... | app/routes/job_execution_control_contracts.py |
| JobMutationResultResponse | BaseModel | job_id: str, operation: Literal['rerun', 'run_to', 'continue', 'delete', 'pac... | app/routes/job_operation_contracts.py |
| BatchJobMutationResponse | BaseModel | results: list[JobMutationResultResponse] | app/routes/job_operation_contracts.py |
| JobBatchRerunRequest | BaseModel | job_ids: list[str], node_key: str | None, from_failed_node: bool | app/routes/job_operation_contracts.py |
| BatchJobIdsRequest | BaseModel | job_ids: list[str] | app/routes/job_operation_contracts.py |
| RunToRequest | BaseModel | target_node_key: str, start_node_key: str | None | app/routes/job_operation_contracts.py |
| ContinueJobRequest | BaseModel | — | app/routes/job_operation_contracts.py |
| BatchRunToRequest | BaseModel | job_ids: list[str], target_node_key: str, start_node_key: str | None | app/routes/job_operation_contracts.py |
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
| OpsMetricsResponse | BaseModel | granularity: Literal['minute', 'hour', 'day'], buckets: list[MetricBucket] | app/routes/metrics_contracts.py |
| WorkspacePackageRequest | BaseModel | job_ids: list[str] | app/routes/package_contracts.py |
| WorkspacePackageResultResponse | BaseModel | job_id: str, status: Literal['succeeded', 'failed'], reason_code: str | None,... | app/routes/package_contracts.py |
| WorkspacePackageResponse | BaseModel | results: list[WorkspacePackageResultResponse], succeeded_count: int, failed_c... | app/routes/package_contracts.py |
| WorkspacePackageStatusResetResponse | BaseModel | results: list[WorkspacePackageResultResponse], succeeded_count: int, failed_c... | app/routes/package_contracts.py |
| WorkspacePackageUpdate | BaseModel | name: str | None, locked: bool | None | app/routes/package_history_contracts.py |
| WorkspacePackageItemResponse | BaseModel | id: int, name: str, path: str, video_count: int, size_bytes: int, locked: int... | app/routes/package_history_contracts.py |
| WorkspacePackagesResponse | BaseModel | packages: list[WorkspacePackageItemResponse] | app/routes/package_history_contracts.py |
| WorkspacePackageDeleteResponse | BaseModel | deleted: bool | app/routes/package_history_contracts.py |
| WorkspacePackageUpdateResponse | BaseModel | id: int, name: str | None, locked: bool | None | app/routes/package_history_contracts.py |
| QuestionNormalized | BaseModel | stem: str | None, options: list[dict[str, Any]] | None, answer: Any | None, a... | app/routes/questions.py |
| QuestionDetailResponse | BaseModel | question_id: str, title: str, normalized: QuestionNormalized, cms_payload: di... | app/routes/questions.py |
| SkillFileResponse | BaseModel | path: str, size: int, content: str, truncated: bool | app/routes/skill_contracts.py |
| SkillDetailResponse | BaseModel | key: str, ref: str, commit: str, available: bool, files: list[SkillFileRespon... | app/routes/skill_contracts.py |
| TokenUsageRunItem | BaseModel | run_id: int, node_key: str, status: str, usage: RunUsage | None, reason: str ... | app/routes/token_usage_contracts.py |
| TokenUsageTotal | BaseModel | message_count: int, input_tokens: int, output_tokens: int, cache_read_tokens:... | app/routes/token_usage_contracts.py |
| TokenUsageJobResponse | BaseModel | job_id: str, runs: list[TokenUsageRunItem], total: TokenUsageTotal, runs_with... | app/routes/token_usage_contracts.py |
| TokenUsageWorkspaceResponse | BaseModel | workspace_id: str, currency: str, summary: TokenUsageSummary, groups: list[To... | app/routes/token_usage_contracts.py |
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
| WorkflowTerminalResponse | BaseModel | outcome: str | app/routes/workflow_node_contracts.py |
| WorkflowNodeExecutionResponse | BaseModel | provider: str, model: str, thinking: str, prompt: str | app/routes/workflow_node_contracts.py |
| WorkflowNodeResponse | BaseModel | key: str, label: str, capability: str, after: list[str], inputs: list[str], o... | app/routes/workflow_node_contracts.py |
| WorkflowRevisionSummary | BaseModel | id: str, workspace_id: str, workflow_key: str, version: int, status: str, def... | app/routes/workflow_revisions_contracts.py |
| WorkflowRevisionsResponse | BaseModel | revisions: list[WorkflowRevisionSummary] | app/routes/workflow_revisions_contracts.py |
| WorkflowDraftRequest | BaseModel | definition_yaml: str | app/routes/workflow_revisions_contracts.py |
| WorkflowDraftValidationResponse | BaseModel | valid: bool, errors: list[str] | app/routes/workflow_revisions_contracts.py |
| ActiveWorkflowRevisionResponse | BaseModel | revision: WorkflowRevisionSummary, workflow: workflow_contracts.WorkflowDefin... | app/routes/workflow_revisions_contracts.py |
| WorkflowRevisionDetailResponse | BaseModel | revision: WorkflowRevisionSummary, workflow: workflow_contracts.WorkflowDefin... | app/routes/workflow_revisions_contracts.py |
| WorkspaceRecord | BaseModel | id: str, name: str, description: str, default_workflow_key: str, default_enti... | app/routes/workspace_contracts.py |
| ResourceProviderDefinition | BaseModel | key: str, provider: str, path: str, defaultParams: dict[str, str], paramKeys:... | app/routes/workspace_contracts.py |
| CmsServiceStatus | BaseModel | baseUrl: str, tokenConfigured: bool, env: str, healthy: bool | None, lastChec... | app/routes/workspace_contracts.py |
| ResourceBinding | BaseModel | enabled: bool, config: dict[str, Any] | app/routes/workspace_contracts.py |
| WorkspaceSecretSetRequest | BaseModel | value: str | app/routes/workspace_secrets.py |
| WorkspaceSecretMetadata | BaseModel | name: str, created_at: str, updated_at: str | app/routes/workspace_secrets.py |
| WorkspaceSecretsResponse | BaseModel | secrets: list[WorkspaceSecretMetadata] | app/routes/workspace_secrets.py |
| WorkspaceSecretResponse | BaseModel | secret: WorkspaceSecretMetadata | app/routes/workspace_secrets.py |
| WorkspaceSecretDeleteResponse | BaseModel | deleted: str | app/routes/workspace_secrets.py |
| JobDeleteResult | TypedDict | job_id: str, operation: str, status: str, reason_code: str | None, message: s... | app/services/job_deletion.py |
| LogEntry | TypedDict | type: str, title: str, detail: str, truncated: bool | app/services/job_log_renderer.py |
| CostBreakdown | BaseModel | currency: str, input: float, output: float, cache_read: float, total: float, ... | app/services/token_usage_contracts.py |
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

  ratchet 脚本不会提高 ceiling；超出预算的文件必须拆分或回退。此外 production 文件有
  800 行绝对上限（`production.max_lines`），豁免也不能突破；挂账超过 30 天的豁免由
  `scripts/check_exemption_age.py` 在 full gate 中告警（不阻断）。

## Related Specs

- [Worker 轮询性能](../superpowers/completed/2026-05-29-worker-polling-performance-design.md)
- [数据库性能优化](../superpowers/completed/2026-05-29-database-performance-design.md)

## Runtime Architecture

### 后端

- `server.app.main:create_app(data_dir, start_worker)` 是 FastAPI 应用工厂。
- 当 `start_worker=True` 时，生命周期内启动 `WorkflowWorkerThread`：
  - 在 `config/workflow.yaml` 中 `workflows.enabled` 为 `true` 时轮询 Agent Legion DAG 任务。
  - 视频 Job 由 `video_knowledge` workflow 的 handler 节点（`download_video`、`transcribe_video`、Agent 阶段、`assemble_video_metadata`、`package_video_job`）处理。
- worker 默认处于**暂停**状态；调用 `POST /api/worker/resume` 开始处理。
- 视频 Job 的 `content_type` 固定为 `knowledge`（`video_capabilities/contracts.py` 强制校验），pipeline 节点序列：

  **Knowledge videos (`knowledge`):**
  1. `download_video` — 下载 MP4
  2. `transcribe_video` — 生成 `subtitles.srt` 与 `transcription.json`
  3. `subtitle_review` — openclaw agent
  4. `chapter_generate` — openclaw agent
  5. `interaction_generate` — openclaw agent
  6. `content_review` — openclaw agent
  7. `assemble_video_metadata` — 生成 `metadata.json`、`report.md`
  8. `package_video_job` — 创建 ZIP package

- 可以提交空 URL 的视频，系统会记录为 `status: missing_url`、`current_phase: waiting_for_url`，worker 会跳过直到补 URL。
- 任一 node 失败会把 Job 置为 `failed`，错误写入数据库与日志文件。
- 支持从任意 node 重跑；重跑会清除该 node 及下游所有 artifacts。
- `DELETE /api/jobs/{job_id}` 会级联删除 Job 记录、`node_runs`、本地 Job 目录与日志。

## Database

- PostgreSQL 同时服务视频 pipeline 与 Agent Legion workflow（当前 `SCHEMA_VERSION = 16`）：
  - `videos` — 旧版视频队列（迁移后仅读）
  - `workspaces` — Agent Legion workspace 定义（含 `default_workflow_key`, `resource_config_json`, `default_entity`, `intake_config_json`）。`resource_config_json` 里 schema 标记 `secret: true` 的字段只存 `{"secret_ref": "<name>"}` 引用，明文不落库（兼容窗口内老明文仍可读，见下文 Secrets Vault）
  - `workspace_secrets` — vault 加密存储的 workspace 密钥（Fernet 密文，`(workspace_id, name)` 唯一，v16 新增）
  - `job_batches`, `jobs`, `job_nodes`, `node_runs` — DAG job 相关表
  - `workflow_revisions` — workflow 版本修订历史
  - `packages` — 已创建 package 路径
- 初始化器在 PostgreSQL advisory lock 下按版本应用 schema；旧 SQLite 数据通过一次性离线导入器迁移。
- `JobQueries.connect()` 与 `WorkspaceQueries.connect()` 是上下文管理器，确保 `conn.close()`。
- `JobDeletionService` 级联删除 Job 记录、`node_runs`、本地 Job 目录与日志。
- 存储路径以**相对 POSIX 路径**保存在 `settings.data_dir` 下（前缀为 `videos/`, `jobs/`, `logs/`, `packages/`），API 返回时投影为绝对路径。

## New Subsystems

### Workflow Studio & Workflow Revisions

Workflow Studio 提供可视化 workflow 编辑能力，与版本修订历史集成。

- **Routes**: `routes/workflow_revisions.py`, `routes/workflow_draft_compare.py`
- **Services**: `services/workflow_drafts.py`, `services/workflow_draft_publish.py`, `services/workflow_revision_format.py`, `services/job_workflow_versions.py`, `services/job_workflow_upgrade.py`; `/api/executors` 同时返回独立的 Agent Catalog 投影，供编辑器按 capability 获取 runtime、skill、tools 与 provider/model/thinking 默认值
- **DB**: PostgreSQL `workflow_revisions` 表与版本化 schema 初始化
- **Frontend**: `pages/WorkflowStudioPage.tsx`, `pages/workflowStudio/`

### Token Usage

Token Usage 收集并展示 Pi agent 节点运行时的 token 消耗与成本。

- **Routes**: `routes/token_usage.py` (`/jobs/{job_id}/token-usage`, `/jobs/{job_id}/runs/{run_id}/token-usage`, `/workspaces/{workspace_id}/token-usage`)
- **Services**: `services/token_usage*.py`
- **Config**: `config/app.yaml` 中的 `token_usage.currency` 与 `token_usage.pricing`
- **Frontend**: `pages/TokenUsagePage.tsx`, `components/TokenUsage*.tsx`

### Configuration Package

`server/app/configuration/` 负责加载并校验按领域拆分的 YAML 配置。

- `loader.py`: 加载 `config/app.yaml`, `config/video_hive.yaml`, `config/workflow.yaml` 并合并环境变量覆盖。
- `owned_keys.py`: 声明每个配置文件的 owned keys，防止跨文件键冲突。

### Quality Subsystem

`server/app/quality/` 在运行时检查架构不变量与豁免。

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
- **写入链**: resource binding 保存时，provider `config_schema` 标记 `secret: true` 的字段值转存 vault，binding config 只留 `{"secret_ref": "resource:{resource_key}:{field}"}`；settings payload 中 secret 字段只返回 `{"secret_set": bool}`。
- **运行时解析**: `resolve_secret_refs` 在 server 端把 `secret_ref` 解析为明文（仅内存；字符串明文透传为兼容窗口）；intake 冻结的是 `secret_ref` 而非明文；`job_logs` 脱敏并入 vault 明文。

## Configuration Reference

配置按域拆分为三个文件：

- `config/app.yaml`：应用路径、HTTP 设置、worker 并发。
- `config/video_hive.yaml`：ASR、CMS、资源提供者、清理、OpenClaw 设置。
- `config/workflow.yaml`：workspace executor 与 workflow 运行时设置。

常用 `config/video_hive.yaml` 配置项：

- `asr.provider`: `auto`, `whisper`, `sensevoice`
- `asr.whisper.binary`: 本地 `whisper-cli` 路径
- `asr.whisper.model`: 本地 whisper 模型路径
- `asr.whisper.vad_model`: 可选 VAD 模型路径
- `asr.sensevoice.script`: SenseVoice 转写脚本路径
- `asr.sensevoice.model_dir`: `SenseVoiceSmall` 模型目录
- `resource_providers`: 资源提供者声明（如 `cms.question.detail`）：`path` 拼接信息、`resource_key`、legacy 设置项 `url_key`，以及可调参数的 `config_schema`（含 `secret: true` 标记）；声明在加载时校验，非法声明启动失败
- `openclaw.command_template`: 含 `{prompt_text}`, `{video_id}`, `{timestamp}` 的命令参数列表
- `openclaw.timeout_seconds`: 默认 600 秒
- `openclaw.skill_safety`: OpenClaw skill 安全校验配置

`config/app.yaml` 额外配置项：

- `data_dir`: 数据根目录
- `server.cors`: 浏览器跨域来源和 credentials 策略
- `cleanup.log_retention_days` / `run_dir_retention_days` / `interval_seconds`: 日志与运行目录清理策略
- `token_usage.currency` / `token_usage.pricing`: Token 用量货币与模型单价

`config/workflow.yaml` 核心配置项：

- `executors`: local / pi / openclaw 执行器定义；local capability 除 `handler` 外可声明 `config_schema`（与 `AgentDefinition.config_schema` 同一子集），节点可调参数经 node_config 解析链注入 handler runtime 的 `node_config` 键
- `workflows.enabled`: 是否启用 Agent Legion DAG workflow worker
- `workflows.pi`: Pi agent runner 配置（provider, model, timeout, environment）

其他配置文件：

- `config/skills.yaml` / `config/skills.lock`：外部 Pi skill 仓库源与固定 commit。
- `config/workflows/*.yaml`：workflow 定义，Node 只声明 `capability`，不声明 `runner`/`agent`/`skill`。
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
- OpenClaw 命令通过 `subprocess.Popen(argv, shell=False)` 执行，模板来自用户可写的 `config/video_hive.yaml`；`{prompt_text}` 替换前经 null 字节剔除与 `shlex.quote` 清洗，OpenClaw skill 仓库在每次运行前强制 checkout 回锁定 ref 并剥离 `GIT_*` 环境变量；仍需确保该配置文件不被未信任用户修改。
- PostgreSQL 与视频存储部署在受信网络内；业务 API 均需登录（cookie session 或 Bearer token，见 README 的 User Authentication 章节），uvicorn 默认绑定 127.0.0.1，启动脚本与 Makefile 均显式固定 `--host 127.0.0.1`。不要用 `--host 0.0.0.0` 把开发服务器暴露到局域网或任何不可信网络——暴露后任何通过鉴权的用户都可删除 job、下载产物、触发执行。
- Workspace 凭证（如 CMS token）经 vault 加密落库（`workspace_secrets`，Fernet），API 永不返回明文，配置与 intake 快照只存 `secret_ref`；master key 走 env / 文件注入，不进 DB、不进日志（VAULT-SECRET-001）。
- `data/` 已加入 `.gitignore`，禁止提交运行时数据或密钥。
