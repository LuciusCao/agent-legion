# 后端架构

## Overview

Video Hive 后端基于 FastAPI，提供 REST API 和 SSE 事件推送。核心职责包括：

- 视频队列管理（ intake → 下载 → 转录 → Agent 阶段 → 打包）
- Agent Legion DAG 流水线执行（Workspace / Job / Node）
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
├── pipelines/              # Agent Legion DAG 定义与执行
│   ├── definition.py       # 流水线定义解析
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
├── worker*.py              # 后台工作线程（视频 + 流水线）
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
- 视频流水线与 Agent Legion 流水线使用独立的 Worker 线程，避免相互阻塞。
- 所有文件 I/O 限制在 `data/` 目录内，由 `security.py` 做路径校验。

## API Surface / Interface

<!-- AUTO-GENERATED: scripts/generate_architecture.py -->

### REST API 路由

> 所有路由挂载在 `/api` 前缀下。

| 方法 | 路径 | 处理函数 | 文件 |
|------|------|----------|------|
| GET | `/agents` | `list_agents` | routes/agents.py |
| POST | `/agents/{agent_id}/assign` | `assign_agent` | routes/agents.py |
| DELETE | `/agents/{agent_id}/assign` | `unassign_agent` | routes/agents.py |
| GET | `/videos/{video_id}/artifacts` | `artifacts` | routes/artifacts.py |
| GET | `/health` | `health` | routes/common.py |
| POST | `/worker/tick` | `worker_tick` | routes/common.py |
| GET | `/resource-providers` | `get_resource_providers` | routes/jobs.py |
| GET | `/global-services` | `get_global_services` | routes/jobs.py |
| GET | `/pipelines` | `list_pipelines` | routes/jobs.py |
| GET | `/pipelines/{pipeline_key}` | `get_pipeline` | routes/jobs.py |
| GET | `/workspaces` | `list_workspaces` | routes/jobs.py |
| POST | `/workspaces` | `create_workspace` | routes/jobs.py |
| GET | `/workspaces/{workspace_id}` | `get_workspace` | routes/jobs.py |
| GET | `/workspaces/{workspace_id}/agents` | `get_workspace_agents` | routes/jobs.py |
| POST | `/workspaces/{workspace_id}/agents` | `set_workspace_agent` | routes/jobs.py |
| GET | `/workspaces/{workspace_id}/settings` | `get_workspace_settings` | routes/jobs.py |
| PUT | `/workspaces/{workspace_id}/configuration` | `replace_workspace_configuration` | routes/jobs.py |
| PATCH | `/workspaces/{workspace_id}/settings/{section}` | `update_workspace_settings_section` | routes/jobs.py |
| POST | `/workspaces/{workspace_id}/settings/test-connection` | `test_workspace_connection` | routes/jobs.py |
| PATCH | `/workspaces/{workspace_id}` | `update_workspace` | routes/jobs.py |
| GET | `/workspaces/{workspace_id}/stats` | `get_workspace_stats` | routes/jobs.py |
| DELETE | `/workspaces/{workspace_id}` | `delete_workspace` | routes/jobs.py |
| POST | `/workspaces/{workspace_id}/job-batches` | `create_workspace_job_batch` | routes/jobs.py |
| GET | `/workspaces/{workspace_id}/jobs` | `list_workspace_jobs` | routes/jobs.py |
| POST | `/workspaces/{workspace_id}/jobs/batch-rerun` | `batch_rerun_workspace_jobs` | routes/jobs.py |
| DELETE | `/workspaces/{workspace_id}/jobs/batch` | `batch_delete_workspace_jobs` | routes/jobs.py |
| GET | `/workspaces/{workspace_id}/runs` | `list_workspace_runs` | routes/jobs.py |
| GET | `/workspaces/{workspace_id}/dag` | `get_workspace_dag` | routes/jobs.py |
| POST | `/job-batches` | `create_job_batch` | routes/jobs.py |
| GET | `/jobs` | `list_jobs` | routes/jobs.py |
| GET | `/jobs/{job_id}` | `get_job` | routes/jobs.py |
| GET | `/jobs/{job_id}/artifacts/{artifact_name:path}` | `get_artifact` | routes/jobs.py |
| POST | `/jobs/{job_id}/nodes/{node_key}/rerun` | `rerun_node` | routes/jobs.py |
| DELETE | `/jobs/{job_id}` | `delete_job` | routes/jobs.py |
| GET | `/jobs/{job_id}/{invalid_path:path}` | `reject_invalid_job_subpath` | routes/jobs.py |
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

### 数据模型

| 模型 | 类型 | 字段 | 文件 |
|------|------|------|------|
| VideoRecord | TypedDict | id: str, source_url: str, title: str, content_type: str, external_id: str, kn... | app/records.py |
| PhaseRunRecord | TypedDict | id: int, video_id: str, phase_key: str, status: str, started_at: str, finishe... | app/records.py |
| AgentStatusResponse | BaseModel | id: str, name: str, busy: bool, current_video_id: str | None, current_title: ... | app/routes/agents.py |
| AgentsResponse | BaseModel | agents: list[AgentStatusResponse] | app/routes/agents.py |
| AgentAssignmentResponse | BaseModel | agent_id: str, workspace_id: str, concurrency_limit: int | app/routes/agents.py |
| AgentUnassignmentResponse | BaseModel | agent_id: str, workspace_id: str, removed: bool | app/routes/agents.py |
| HealthResponse | BaseModel | ok: bool | app/routes/common.py |
| JobBatchRequest | BaseModel | pipeline_key: str, entity: str | None, source_kind: str, question_ids: list[s... | app/routes/jobs.py |
| JobBatchResponse | BaseModel | batch: dict[str, Any], created_count: int, jobs: list[dict[str, Any]] | app/routes/jobs.py |
| JobsResponse | BaseModel | jobs: list[dict[str, Any]] | app/routes/jobs.py |
| PipelineResponse | BaseModel | pipeline: dict[str, Any] | app/routes/jobs.py |
| PipelinesListResponse | BaseModel | pipelines: list[dict[str, Any]] | app/routes/jobs.py |
| WorkspaceCreateRequest | BaseModel | name: str, default_pipeline_key: str, default_entity: str, cms_config: dict[s... | app/routes/jobs.py |
| WorkspaceUpdateRequest | BaseModel | name: str | None, description: str | None, default_pipeline_key: str | None, ... | app/routes/jobs.py |
| WorkspaceSettingsResponse | BaseModel | settings: dict[str, Any] | app/routes/jobs.py |
| WorkspaceSettingsSectionRequest | BaseModel | cmsUrl: str | None, cmsToken: str | None, entityType: str | None, intakeModes... | app/routes/jobs.py |
| WorkspaceSettingsTestResponse | BaseModel | ok: bool, message: str | app/routes/jobs.py |
| WorkspaceResponse | BaseModel | workspace: dict[str, Any] | app/routes/jobs.py |
| WorkspacesResponse | BaseModel | workspaces: list[dict[str, Any]] | app/routes/jobs.py |
| WorkspaceAgentsResponse | BaseModel | agents: list[dict[str, Any]] | app/routes/jobs.py |
| WorkspaceAgentAssignmentResponse | BaseModel | agent_id: str, workspace_id: str, concurrency_limit: int | app/routes/jobs.py |
| DeleteJobResponse | BaseModel | deleted: str | app/routes/jobs.py |
| JobDetailResponse | BaseModel | job: dict[str, Any], nodes: list[dict[str, Any]], runs: list[dict[str, Any]],... | app/routes/jobs.py |
| ArtifactResponse | BaseModel | name: str, content: str | app/routes/jobs.py |
| RerunNodeResponse | BaseModel | job_id: str, node_key: str, stale_nodes: list[str] | app/routes/jobs.py |
| BatchJobRequest | BaseModel | job_ids: list[str] | app/routes/jobs.py |
| BatchJobResponse | BaseModel | results: list[dict[str, Any]] | app/routes/jobs.py |
| WorkspaceRunsResponse | BaseModel | runs: list[dict[str, Any]] | app/routes/jobs.py |
| WorkspaceDagResponse | BaseModel | pipeline: dict[str, Any], nodes: list[dict[str, Any]] | app/routes/jobs.py |
| WorkspaceAgentConfig | BaseModel | agent_id: str, concurrency_limit: int | app/routes/jobs.py |
| WorkspaceConfigurationSettingsRequest | BaseModel | entityType: str | None, intakeModes: list[str] | None, labelOverrides: dict[s... | app/routes/jobs.py |
| WorkspaceConfigurationRequest | BaseModel | name: str | None, description: str | None, settings: WorkspaceConfigurationSe... | app/routes/jobs.py |
| WorkspaceConfigurationResponse | BaseModel | workspace: dict[str, Any], settings: dict[str, Any], agents: list[dict[str, A... | app/routes/jobs.py |
| WorkspaceAgentStatus | BaseModel | id: str, name: str, busy: bool | app/routes/jobs.py |
| WorkspaceStatsResponse | BaseModel | workspace_id: str, name: str, pipeline_key: str, pipeline_label: str, job_sta... | app/routes/jobs.py |
| DeleteWorkspaceResponse | BaseModel | deleted: str | app/routes/jobs.py |
| ResourceProvidersResponse | BaseModel | providers: list[dict[str, Any]] | app/routes/jobs.py |
| GlobalServicesResponse | BaseModel | cms: dict[str, Any] | app/routes/jobs.py |
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

<!-- END AUTO-GENERATED -->

## 接口契约与架构守护

- FastAPI 路由必须使用 Pydantic 响应模型，它们是 HTTP 接口的唯一事实来源。
- `scripts/export_openapi.py` 在不启动 Worker 的情况下导出 OpenAPI 模式。
- `frontend/src/generated/api.ts` 由 OpenAPI 模式生成，并通过 `npm run api:check` 做漂移检查；禁止手写重复的传输类型。
- `scripts/check_architecture.py` 在质量门禁中执行，负责约束模块边界与体积预算。

## Related Specs

- [Worker 轮询性能](../superpowers/completed/2026-05-29-worker-polling-performance-design.md)
- [数据库性能优化](../superpowers/completed/2026-05-29-database-performance-design.md)
