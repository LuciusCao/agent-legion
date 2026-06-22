# 后端架构

## Overview

Video Hive 后端基于 FastAPI，提供 REST API 和 SSE 事件推送。核心职责包括：

- 视频队列管理（ intake → 下载 → 转录 → Agent 阶段 → 打包）
- Agent Legion DAG 工作流执行（Workspace / Job / Node）
- CMS 集成（知识库与题库查询）
- SQLite 持久化与本地文件系统管理

## Directory Structure

```
server/app/
├── main.py                 # FastAPI 应用工厂 + 生命周期
├── routes/                 # REST API 路由
│   ├── videos.py           # 视频 CRUD 与批量操作
│   ├── jobs.py             # Agent Legion Job API
│   ├── packages.py         # 打包管理
│   ├── agents.py           # Agent 状态查询
│   └── worker.py           # Worker 控制（暂停/恢复）
├── services/               # 业务逻辑服务层
│   ├── intake.py           # 视频入库
│   ├── video_actions.py    # 批量操作
│   ├── manual_run.py       # 手动阶段运行
│   └── interaction_stats.py# 交互统计
├── pipeline/               # 视频处理流水线阶段
│   ├── download.py         # HTTP 下载
│   ├── transcribe.py       # ASR 转录
│   ├── openclaw.py         # OpenClaw Agent 调用
│   ├── assemble.py         # 元数据组装
│   └── package.py          # ZIP 打包
├── workflows/              # Agent Legion DAG 定义与执行
│   ├── definition.py       # 工作流定义解析
│   ├── scheduler.py        # DAG 调度
│   ├── executor.py         # 节点执行
│   └── pi_runner.py        # Pi Agent 运行器
├── db/                     # 数据库层
│   ├── schema.py           # 表结构定义
│   ├── queries.py          # 视频相关查询
│   └── notifications.py    # SSE 通知
├── cms/                    # CMS 客户端
│   ├── auth.py             # 认证
│   ├── client.py           # HTTP 客户端
│   ├── knowledge.py        # 知识库查询
│   └── question.py         # 题库查询
├── worker*.py              # 后台工作线程（视频 + 工作流）
└── agents.py               # Agent 发现与状态跟踪
```

## Data Flow

```
客户端请求 → FastAPI Router → Service Layer → DB / Pipeline / CMS
                     ↓
               SSE Events ← DB Notifications
                     ↓
               前端实时更新
```

后台 Worker 线程定期轮询数据库，驱动视频从 `queued` 状态向 `completed` 状态推进。

## Key Decisions

- 使用 SQLite 作为本地数据库，避免外部依赖。详见相关 spec。
- 视频流水线与 Agent Legion 工作流使用独立的 Worker 线程，避免相互阻塞。
- 所有文件 I/O 限制在 `data/` 目录内，由 `security.py` 做路径校验。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### REST API 路由

> 所有路由挂载在 `/api` 前缀下。

| 方法 | 路径 | 处理函数 | 文件 |
|------|------|----------|------|
| GET | `/agents` | `list_agents` | routes/agents.py |
| GET | `/videos/{video_id}/artifacts` | `artifacts` | routes/artifacts.py |
| GET | `/health` | `health` | routes/common.py |
| POST | `/worker/tick` | `worker_tick` | routes/common.py |
| GET | `/jobs/{job_id}/artifacts/{artifact_name:path}` | `get_artifact` | routes/job_artifacts.py |
| GET | `/jobs/{job_id}/runs/{run_id}/log` | `get_job_run_log` | routes/job_artifacts.py |
| GET | `/jobs/{job_id}/{invalid_path:path}` | `reject_invalid_job_subpath` | routes/job_artifacts.py |
| POST | `/workspaces/{workspace_id}/job-batches` | `create_workspace_job_batch` | routes/job_batches.py |
| POST | `/job-batches` | `create_job_batch` | routes/job_batches.py |
| GET | `/workspaces/{workspace_id}/jobs` | `list_workspace_jobs` | routes/jobs.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-rerun` | `batch_rerun_workspace_jobs` | routes/jobs.py |
| DELETE | `/workspaces/{workspace_id}/jobs/batch` | `batch_delete_workspace_jobs` | routes/jobs.py |
| GET | `/jobs` | `list_jobs` | routes/jobs.py |
| GET | `/jobs/{job_id}` | `get_job` | routes/jobs.py |
| POST | `/jobs/{job_id}/nodes/{node_key}/rerun` | `rerun_node` | routes/jobs.py |
| DELETE | `/jobs/{job_id}` | `delete_job` | routes/jobs.py |
| POST | `/jobs/{job_id}/run-to` | `run_to` | routes/jobs.py |
| POST | `/jobs/{job_id}/continue` | `continue_job` | routes/jobs.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-run-to` | `batch_run_to` | routes/jobs.py |
| POST | `/package` | `package_completed` | routes/packages.py |
| GET | `/packages` | `list_packages` | routes/packages.py |
| DELETE | `/packages/{package_id:int}` | `delete_package` | routes/packages.py |
| PATCH | `/packages/{package_id:int}` | `update_package` | routes/packages.py |
| GET | `/packages/{filename:path}` | `download_package` | routes/packages.py |
| GET | `/workspaces/{workspace_id}/packages` | `list_workspace_packages` | routes/packages.py |
| POST | `/workspaces/{workspace_id}/jobs/package` | `package_workspace_jobs` | routes/packages.py |
| GET | `/workspaces/{workspace_id}/packages/{filename:path}` | `download_workspace_package` | routes/packages.py |
| GET | `/workspaces/{workspace_id}/questions/{question_id}` | `get_question_detail` | routes/questions.py |
| GET | `/video-hive/config` | `get_video_hive_config` | routes/video_hive.py |
| POST | `/videos` | `add_videos` | routes/videos.py |
| GET | `/videos` | `list_videos` | routes/videos.py |
| GET | `/videos/{video_id}` | `get_video` | routes/videos.py |
| POST | `/videos/batch/delete` | `batch_delete_videos` | routes/videos.py |
| POST | `/videos/batch/rerun` | `batch_rerun_videos` | routes/videos.py |
| POST | `/videos/batch/run-to` | `batch_run_to_videos` | routes/videos.py |
| POST | `/videos/{video_id}/run-to` | `run_video_to_phase` | routes/videos.py |
| POST | `/videos/{video_id}/rerun` | `rerun_video` | routes/videos.py |
| DELETE | `/videos/{video_id}` | `delete_video` | routes/videos.py |
| GET | `/videos/{video_id}/logs` | `logs` | routes/videos.py |
| GET | `/videos/{video_id}/phase-runs/{run_id}/session` | `phase_run_session` | routes/videos.py |
| GET | `/videos/{video_id}/video` | `video_file` | routes/videos.py |
| HEAD | `/videos/{video_id}/video` | `video_file` | routes/videos.py |
| GET | `/worker/status` | `worker_status` | routes/worker.py |
| POST | `/worker/pause` | `pause_worker` | routes/worker.py |
| POST | `/worker/resume` | `resume_worker` | routes/worker.py |
| GET | `/resource-providers` | `get_resource_providers` | routes/workflow_catalog.py |
| GET | `/global-services` | `get_global_services` | routes/workflow_catalog.py |
| GET | `/workflows` | `list_workflows` | routes/workflow_catalog.py |
| GET | `/workflows/{workflow_key}` | `get_workflow` | routes/workflow_catalog.py |
| PUT | `/workspaces/{workspace_id}/configuration` | `replace_workspace_configuration` | routes/workspace_configuration.py |
| GET | `/executors` | `get_executors` | routes/workspace_executors.py |
| GET | `/workspaces/{workspace_id}/executor-configuration` | `get_workspace_executor_configuration` | routes/workspace_executors.py |
| GET | `/workspaces/{workspace_id}/runs` | `list_workspace_runs` | routes/workspace_runs.py |
| GET | `/workspaces/{workspace_id}/dag` | `get_workspace_dag` | routes/workspace_runs.py |
| GET | `/workspaces/{workspace_id}/settings` | `get_workspace_settings` | routes/workspace_settings.py |
| PATCH | `/workspaces/{workspace_id}/settings/{section}` | `update_workspace_settings_section` | routes/workspace_settings.py |
| POST | `/workspaces/{workspace_id}/settings/test-connection` | `test_workspace_connection` | routes/workspace_settings.py |
| GET | `/workspaces` | `list_workspaces` | routes/workspaces.py |
| POST | `/workspaces` | `create_workspace` | routes/workspaces.py |
| GET | `/workspaces/{workspace_id}` | `get_workspace` | routes/workspaces.py |
| PATCH | `/workspaces/{workspace_id}` | `update_workspace` | routes/workspaces.py |
| DELETE | `/workspaces/{workspace_id}` | `delete_workspace` | routes/workspaces.py |
| GET | `/workspaces/{workspace_id}/stats` | `get_workspace_stats` | routes/workspaces.py |

### 数据模型

| 模型 | 类型 | 字段 | 文件 |
|------|------|------|------|
| LocalCapabilityConfig | BaseModel | handler: str | app/executors/config.py |
| PiCapabilityConfig | BaseModel | skill: str, tools: tuple[str, ...] | app/executors/config.py |
| OpenClawCapabilityConfig | BaseModel | skill: str | app/executors/config.py |
| LocalExecutorConfig | BaseModel | kind: Literal['local'], global_capacity: int, capabilities: dict[str, LocalCa... | app/executors/config.py |
| PiExecutorConfig | BaseModel | kind: Literal['pi'], global_capacity: int, capabilities: dict[str, PiCapabili... | app/executors/config.py |
| OpenClawExecutorConfig | BaseModel | kind: Literal['openclaw'], agent_id: str, global_capacity: int, capabilities:... | app/executors/config.py |
| PiRuntimeConfig | BaseModel | binary: str, provider: str, model: str, thinking: str, timeout_seconds: int, ... | app/executors/runtime_config.py |
| OpenClawSkillSafetyRuntimeConfig | BaseModel | enabled: bool, repos: list[dict[str, str]] | app/executors/runtime_config.py |
| OpenClawRuntimeConfig | BaseModel | command_template: tuple[str, ...], cwd: str, timeout_seconds: int, cancellati... | app/executors/runtime_config.py |
| WorkflowsRuntimeConfig | BaseModel | enabled: bool, pi: PiRuntimeConfig | app/executors/runtime_config.py |
| ExecutorRuntimeConfig | BaseModel | cancellation_grace_seconds: int, workflows: WorkflowsRuntimeConfig, openclaw:... | app/executors/runtime_config.py |
| VideoRecord | TypedDict | id: str, source_url: str, title: str, content_type: str, external_id: str, kn... | app/records.py |
| PhaseRunRecord | TypedDict | id: int, video_id: str, phase_key: str, status: str, started_at: str, finishe... | app/records.py |
| AgentStatusResponse | BaseModel | id: str, name: str, busy: bool, current_video_id: str | None, current_title: ... | app/routes/agents.py |
| AgentsResponse | BaseModel | agents: list[AgentStatusResponse] | app/routes/agents.py |
| HealthResponse | BaseModel | ok: bool | app/routes/common.py |
| ExecutorDefinitionResponse | BaseModel | id: str, kind: Literal['local', 'pi', 'openclaw'], global_capacity: int, capa... | app/routes/executor_contracts.py |
| ExecutorCatalogResponse | BaseModel | executors: list[ExecutorDefinitionResponse] | app/routes/executor_contracts.py |
| ExecutorAllocationRequest | BaseModel | executor_id: str, concurrency_limit: int | app/routes/executor_contracts.py |
| NodeBindingRequest | BaseModel | workflow_key: str, node_key: str, executor_id: str | app/routes/executor_contracts.py |
| NodeLimitRequest | BaseModel | workflow_key: str, node_key: str, concurrency_limit: int | app/routes/executor_contracts.py |
| WorkspaceExecutorConfigurationResponse | BaseModel | allocations: list[ExecutorAllocationResponse], bindings: list[NodeBindingRequ... | app/routes/executor_contracts.py |
| WorkspaceSettingsPayload | BaseModel | entityType: str, intakeModes: list[str], labelOverrides: dict[str, str], work... | app/routes/executor_contracts.py |
| WorkspaceConfigurationSettingsRequest | BaseModel | entityType: str | None, intakeModes: list[str] | None, labelOverrides: dict[s... | app/routes/executor_contracts.py |
| WorkspaceConfigurationRequest | BaseModel | name: str | None, description: str | None, settings: WorkspaceConfigurationSe... | app/routes/executor_contracts.py |
| WorkspaceConfigurationResponse | BaseModel | workspace: dict[str, Any], settings: WorkspaceSettingsPayload, executor_confi... | app/routes/executor_contracts.py |
| JobBatchRequest | BaseModel | workflow_key: str, entity: str | None, source_kind: str, question_ids: list[s... | app/routes/job_contracts.py |
| JobBatchResponse | BaseModel | batch: dict[str, Any], created_count: int, jobs: list[dict[str, Any]] | app/routes/job_contracts.py |
| WorkspaceCreateRequest | BaseModel | name: str, default_workflow_key: str, default_entity: str, cms_config: dict[s... | app/routes/job_contracts.py |
| WorkspaceUpdateRequest | BaseModel | name: str | None, description: str | None, default_workflow_key: str | None, ... | app/routes/job_contracts.py |
| WorkspaceSettingsResponse | BaseModel | settings: dict[str, Any] | app/routes/job_contracts.py |
| WorkspaceSettingsSectionRequest | BaseModel | cmsUrl: str | None, cmsToken: str | None, entityType: str | None, intakeModes... | app/routes/job_contracts.py |
| WorkspaceSettingsTestResponse | BaseModel | ok: bool, message: str | app/routes/job_contracts.py |
| WorkspaceResponse | BaseModel | workspace: dict[str, Any] | app/routes/job_contracts.py |
| WorkspacesResponse | BaseModel | workspaces: list[dict[str, Any]] | app/routes/job_contracts.py |
| DeleteJobResponse | BaseModel | deleted: str | app/routes/job_contracts.py |
| ArtifactResponse | BaseModel | name: str, content: str | app/routes/job_contracts.py |
| WorkspaceRunsResponse | BaseModel | runs: list[dict[str, Any]] | app/routes/job_contracts.py |
| WorkspaceDagResponse | BaseModel | workflow: dict[str, Any], nodes: list[dict[str, Any]] | app/routes/job_contracts.py |
| ExecutorRuntimeStatus | BaseModel | executor_id: str, kind: str, global_capacity: int, workspace_limit: int, runn... | app/routes/job_contracts.py |
| ExecutorStatusSummary | BaseModel | executors: list[ExecutorRuntimeStatus] | app/routes/job_contracts.py |
| WorkspaceStatsResponse | BaseModel | workspace_id: str, name: str, workflow_key: str, workflow_label: str, job_sta... | app/routes/job_contracts.py |
| DeleteWorkspaceResponse | BaseModel | deleted: str | app/routes/job_contracts.py |
| ResourceProvidersResponse | BaseModel | providers: list[dict[str, Any]] | app/routes/job_contracts.py |
| GlobalServicesResponse | BaseModel | cms: dict[str, Any] | app/routes/job_contracts.py |
| JobMutationResultResponse | BaseModel | job_id: str, operation: Literal['rerun', 'run_to', 'continue', 'delete', 'pac... | app/routes/job_operation_contracts.py |
| BatchJobMutationResponse | BaseModel | results: list[JobMutationResultResponse] | app/routes/job_operation_contracts.py |
| JobBatchRerunRequest | BaseModel | job_ids: list[str], node_key: str | app/routes/job_operation_contracts.py |
| BatchJobIdsRequest | BaseModel | job_ids: list[str] | app/routes/job_operation_contracts.py |
| WorkspacePackageRequest | BaseModel | job_ids: list[str] | app/routes/job_operation_contracts.py |
| WorkspacePackageResultResponse | BaseModel | job_id: str, status: Literal['succeeded', 'failed'], reason_code: str | None,... | app/routes/job_operation_contracts.py |
| WorkspacePackageResponse | BaseModel | results: list[WorkspacePackageResultResponse], succeeded_count: int, failed_c... | app/routes/job_operation_contracts.py |
| RunToRequest | BaseModel | target_node_key: str, start_node_key: str | None | app/routes/job_operation_contracts.py |
| ContinueJobRequest | BaseModel | — | app/routes/job_operation_contracts.py |
| BatchRunToRequest | BaseModel | job_ids: list[str], target_node_key: str, start_node_key: str | None | app/routes/job_operation_contracts.py |
| ExecutionControlSummaryResponse | BaseModel | mode: Literal['full', 'until_node'], target_node_key: str | None, paused: boo... | app/routes/job_view_contracts.py |
| JobNodeSummaryResponse | BaseModel | node_key: str, label: str, status: str, error_message: str | app/routes/job_view_contracts.py |
| JobSummaryResponse | BaseModel | id: str, workspace_id: str, workflow_key: str, source_type: str, source_id: s... | app/routes/job_view_contracts.py |
| JobsResponse | BaseModel | jobs: list[JobSummaryResponse] | app/routes/job_view_contracts.py |
| JobNodeResponse | BaseModel | id: int, job_id: str, node_key: str, status: str, stale_reason: str, error_me... | app/routes/job_view_contracts.py |
| NodeRunResponse | BaseModel | id: int, job_id: str, node_key: str, status: str, started_at: str, finished_a... | app/routes/job_view_contracts.py |
| JobLogResponse | BaseModel | run_id: int, log: str, truncated: bool | app/routes/job_view_contracts.py |
| JobDetailResponse | BaseModel | job: JobSummaryResponse, nodes: list[JobNodeResponse], runs: list[NodeRunResp... | app/routes/job_view_contracts.py |
| PackageRequest | BaseModel | video_ids: list[str] | None, name: str | None | app/routes/packages.py |
| PackageUpdate | BaseModel | name: str | None, locked: bool | None | app/routes/packages.py |
| PackageResponse | BaseModel | accepted: bool | app/routes/packages.py |
| QuestionNormalized | BaseModel | stem: str | None, options: list[dict[str, Any]] | None, answer: Any | None, a... | app/routes/questions.py |
| QuestionDetailResponse | BaseModel | question_id: str, title: str, normalized: QuestionNormalized, cms_payload: di... | app/routes/questions.py |
| AsrConfigResponse | BaseModel | provider: str, whisper_configured: bool, sensevoice_configured: bool, vad_ena... | app/routes/video_hive.py |
| OpenclawConfigResponse | BaseModel | runner_count: int, timeout_seconds: int | app/routes/video_hive.py |
| VideoHiveConfigResponse | BaseModel | asr: AsrConfigResponse, openclaw: OpenclawConfigResponse | app/routes/video_hive.py |
| VideoInput | BaseModel | url: str, title: str, content_type: str, external_id: str, source_uuid: str | app/routes/videos.py |
| AddVideosRequest | BaseModel | items: list[VideoInput] | app/routes/videos.py |
| RerunRequest | BaseModel | phase: str | app/routes/videos.py |
| RunToRequest | BaseModel | target_phase: str, start_phase: str | None | app/routes/videos.py |
| BatchVideoIdsRequest | BaseModel | video_ids: list[str] | app/routes/videos.py |
| DeleteResult | BaseModel | video_id: str, status: str, message: str | app/routes/videos.py |
| BatchDeleteResponse | BaseModel | results: list[DeleteResult] | app/routes/videos.py |
| BatchRerunResponse | BaseModel | results: list[RerunResult] | app/routes/videos.py |
| RunToSingleResponse | BaseModel | result: RunToResult, video: dict[str, Any] | None | app/routes/videos.py |
| BatchRunToResponse | BaseModel | results: list[RunToResult] | app/routes/videos.py |
| WorkerStatusResponse | BaseModel | paused: bool | app/routes/worker.py |
| WorkflowSummaryResponse | BaseModel | key: str, label: str | app/routes/workflow_contracts.py |
| WorkflowIntakeModeResponse | BaseModel | key: str, label: str, input_field: str, resource: str | app/routes/workflow_contracts.py |
| WorkflowIntakeResponse | BaseModel | modes: list[WorkflowIntakeModeResponse] | app/routes/workflow_contracts.py |
| WorkflowNodeResponse | BaseModel | key: str, label: str, capability: str, after: list[str], inputs: list[str], o... | app/routes/workflow_contracts.py |
| WorkflowResponse | BaseModel | workflow: WorkflowDefinitionResponse | app/routes/workflow_contracts.py |
| WorkflowsListResponse | BaseModel | workflows: list[WorkflowSummaryResponse] | app/routes/workflow_contracts.py |
| JobDeleteResult | TypedDict | job_id: str, operation: str, status: str, reason_code: str | None, message: s... | app/services/job_deletion.py |
| JobPackageItemResult | TypedDict | job_id: str, status: str, reason_code: str | None, message: str | None | app/services/job_packages.py |
| JobPackageResult | TypedDict | results: list[JobPackageItemResult], succeeded_count: int, failed_count: int,... | app/services/job_packages.py |

<!-- END AUTO-GENERATED -->

## 接口契约与架构守护

- FastAPI 路由必须使用 Pydantic 响应模型，它们是 HTTP 接口的唯一事实来源。
- `scripts/export_openapi.py` 在不启动 Worker 的情况下导出 OpenAPI 模式。
- `frontend/src/generated/api.ts` 由 OpenAPI 模式生成，并通过 `npm run api:check` 做漂移检查；禁止手写重复的传输类型。
- `scripts/check_architecture.py` 在质量门禁中执行，负责约束模块边界与体积预算。
- 源文件体积预算由 `config/architecture/architecture-budget-policy.yaml`（人工维护的策略）和
  `config/architecture/architecture-budgets.json`（机器维护的基线）共同治理。基线通过 ratchet 脚本更新：

  ```bash
  UV_CACHE_DIR=.uv-cache uv run python scripts/ratchet_architecture_budgets.py
  UV_CACHE_DIR=.uv-cache uv run python scripts/check_architecture.py
  ```

  ratchet 脚本不会提高 ceiling；超出预算的文件必须拆分或回退。

## Related Specs

- [Worker 轮询性能](../superpowers/completed/2026-05-29-worker-polling-performance-design.md)
- [数据库性能优化](../superpowers/completed/2026-05-29-database-performance-design.md)
