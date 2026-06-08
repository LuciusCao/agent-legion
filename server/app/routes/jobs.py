from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.app.agents import AgentStatusManager
from server.app.cms.client import get_token
from server.app.cms.question import list_questions_by_knowledge
from server.app.jobs import JobQueries
from server.app.pipelines.definition import PipelineDefinition, load_pipeline_definition
from server.app.pipelines.resources import resolve_cms_resource
from server.app.pipelines.scheduler import downstream_nodes
from server.app.settings import Settings


def _candidate(
    entity_type: str,
    entity_id: str,
    title: str,
    source_kind: str,
    source_value: str,
) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "title": title,
        "source": {"kind": source_kind, "value": source_value},
    }


class JobBatchRequest(BaseModel):
    pipeline_key: str = "question_content"
    source_kind: str
    question_ids: list[str] = Field(default_factory=list)
    knowledge_codes: list[str] = Field(default_factory=list)


class JobBatchResponse(BaseModel):
    batch: dict[str, Any]
    created_count: int
    jobs: list[dict[str, Any]]


class JobsResponse(BaseModel):
    jobs: list[dict[str, Any]]


class PipelineResponse(BaseModel):
    pipeline: dict[str, Any]


class WorkspaceCreateRequest(BaseModel):
    name: str
    default_pipeline_key: str = "question_content"
    cms_config: dict[str, Any] = Field(default_factory=dict)
    resource_config: dict[str, Any] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = None
    default_pipeline_key: str | None = None
    cms_config: dict[str, Any] | None = None
    resource_config: dict[str, Any] | None = None


class WorkspaceResponse(BaseModel):
    workspace: dict[str, Any]


class WorkspacesResponse(BaseModel):
    workspaces: list[dict[str, Any]]


class JobDetailResponse(BaseModel):
    job: dict[str, Any]
    nodes: list[dict[str, Any]]
    runs: list[dict[str, Any]]
    artifacts: list[str]


class ArtifactResponse(BaseModel):
    name: str
    content: str


class RerunNodeResponse(BaseModel):
    job_id: str
    node_key: str
    stale_nodes: list[str]


class WorkspaceStatsResponse(BaseModel):
    workspace_id: str
    name: str
    pipeline_key: str
    pipeline_label: str
    job_stats: dict[str, int]
    agent_status: dict[str, int]
    latest_run: dict[str, Any] | None


class DeleteWorkspaceResponse(BaseModel):
    deleted: str


def _pipelines_enabled(settings: Settings) -> bool:
    pipelines = settings.config.get("pipelines", {})
    return isinstance(pipelines, dict) and bool(pipelines.get("enabled"))


def _require_enabled(settings: Settings) -> None:
    if not _pipelines_enabled(settings):
        raise HTTPException(status_code=404, detail="Pipelines are disabled")


def _definition(settings: Settings, pipeline_key: str) -> PipelineDefinition:
    if pipeline_key != "question_content":
        raise HTTPException(status_code=404, detail="Unknown pipeline")
    path = settings.root_dir / "config" / "pipelines" / "question_content.yaml"
    return load_pipeline_definition(path)


def _workspace_or_404(job_db: JobQueries, workspace_id: str) -> dict[str, Any]:
    workspace = job_db.get_workspace(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def _effective_cms_config(settings: Settings, workspace: dict[str, Any]) -> dict[str, Any]:
    base = settings.config.get("cms", {})
    config = dict(base) if isinstance(base, dict) else {}
    workspace_config = workspace.get("cms_config")
    if isinstance(workspace_config, dict):
        config.update(workspace_config)
    return config


def _normalize_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _singular_field_name(value: str) -> str:
    if value.endswith("ies"):
        return f"{value[:-3]}y"
    if value.endswith("s"):
        return value[:-1]
    return value


def _artifact_path(job: dict[str, Any], artifact_name: str) -> Path:
    if "/" in artifact_name or "\\" in artifact_name or artifact_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid artifact name")

    base = Path(str(job["storage_dir"])).resolve()
    path = (base / artifact_name).resolve()
    if path.parent != base:
        raise HTTPException(status_code=400, detail="Invalid artifact path")
    return path


def _artifact_names(job: dict[str, Any]) -> list[str]:
    base = Path(str(job["storage_dir"]))
    if not base.exists():
        return []
    return sorted(path.name for path in base.iterdir() if path.is_file())


def _pipeline_label(settings: Settings, pipeline_key: str) -> str:
    return _definition(settings, pipeline_key).label


def _pipeline_payload(settings: Settings, pipeline_key: str) -> dict[str, Any]:
    definition = _definition(settings, pipeline_key)
    return {
        "key": definition.key,
        "label": definition.label,
        "concurrency": {
            "local": definition.concurrency.local,
            "agent": definition.concurrency.agent,
        },
        "intake": {
            "modes": [
                {
                    "key": mode.key,
                    "label": mode.label,
                    "input_field": mode.input_field,
                    "resource": mode.resource,
                }
                for mode in definition.intake.modes.values()
            ]
        },
        "nodes": [
            {
                "key": node.key,
                "runner": node.runner,
                "after": node.after,
                "inputs": node.inputs,
                "outputs": node.outputs,
            }
            for node in definition.nodes.values()
        ],
    }


def create_jobs_router(
    job_db: JobQueries, settings: Settings, agent_manager: AgentStatusManager
) -> APIRouter:
    router = APIRouter()

    def create_batch_for_workspace(
        workspace_id: str,
        payload: JobBatchRequest,
    ) -> JobBatchResponse:
        _require_enabled(settings)
        workspace = _workspace_or_404(job_db, workspace_id)
        definition = _definition(settings, payload.pipeline_key)
        cms_config = _effective_cms_config(settings, workspace)
        resource_config = workspace.get("resource_config")
        if not isinstance(resource_config, dict):
            resource_config = {}
        mode = definition.intake.modes.get(payload.source_kind)
        if mode is None:
            raise HTTPException(status_code=400, detail="Unsupported intake mode")

        raw_values = getattr(payload, mode.input_field, None)
        if not isinstance(raw_values, list):
            raise HTTPException(
                status_code=400, detail=f"Unsupported input field: {mode.input_field}"
            )
        input_values = _normalize_values(raw_values)
        if not input_values:
            raise HTTPException(
                status_code=400,
                detail=f"At least one {_singular_field_name(mode.input_field)} is required",
            )

        candidates: list[dict[str, Any]] = []
        entity_type = "question"
        if not mode.resource:
            candidates = [
                _candidate(
                    entity_type,
                    value,
                    f"Question {value}",
                    payload.source_kind,
                    value,
                )
                for value in input_values
            ]
        else:
            list_resource = resolve_cms_resource(
                settings.config,
                workspace,
                None,
                mode.resource,
            )
            token = get_token(str(list_resource.get("env", "")), list_resource)
            seen_question_ids: set[str] = set()
            for knowledge_code in input_values:
                summaries = list_questions_by_knowledge(
                    knowledge_code,
                    list_resource.get("api_url") or list_resource.get("question_list_url"),
                    token,
                )
                for summary in summaries:
                    if summary.question_id in seen_question_ids:
                        continue
                    seen_question_ids.add(summary.question_id)
                    candidates.append(
                        _candidate(
                            entity_type,
                            summary.question_id,
                            summary.title or f"Question {summary.question_id}",
                            "knowledge_code",
                            knowledge_code,
                        )
                    )

        if not candidates:
            raise HTTPException(status_code=400, detail="No tasks were resolved from input")

        question_ids = [
            candidate["entity_id"]
            for candidate in candidates
            if candidate["entity_type"] == "question"
        ]
        knowledge_codes = input_values if mode.input_field == "knowledge_codes" else []
        source_payload = payload.model_dump()
        source_payload["question_ids"] = question_ids
        source_payload["knowledge_codes"] = (
            knowledge_codes if mode.input_field == "knowledge_codes" else []
        )
        source_payload["cms_config"] = cms_config
        source_payload["resource_config"] = resource_config
        source_payload["intake_mode"] = {
            "key": mode.key,
            "label": mode.label,
            "input_field": mode.input_field,
            "resource": mode.resource,
        }
        source_payload["task_candidates"] = candidates
        batch = job_db.create_batch(
            payload.pipeline_key,
            payload.source_kind,
            source_payload,
            workspace_id=workspace_id,
        )
        jobs: list[dict[str, Any]] = []
        for candidate in candidates:
            jobs.append(
                job_db.create_job(
                    pipeline_key=payload.pipeline_key,
                    source_type=str(candidate["entity_type"]),
                    source_id=str(candidate["entity_id"]),
                    batch_id=batch["id"],
                    title=str(candidate["title"]),
                    node_keys=list(definition.nodes),
                    workspace_id=workspace_id,
                )
            )

        batch["created_count"] = len(jobs)
        return JobBatchResponse(batch=batch, created_count=len(jobs), jobs=jobs)

    @router.get("/pipelines/{pipeline_key}", response_model=PipelineResponse)
    def get_pipeline(pipeline_key: str) -> PipelineResponse:
        _require_enabled(settings)
        return PipelineResponse(pipeline=_pipeline_payload(settings, pipeline_key))

    @router.get("/workspaces", response_model=WorkspacesResponse)
    def list_workspaces() -> WorkspacesResponse:
        _require_enabled(settings)
        return WorkspacesResponse(workspaces=job_db.list_workspaces())

    @router.post("/workspaces", response_model=WorkspaceResponse)
    def create_workspace(payload: WorkspaceCreateRequest) -> WorkspaceResponse:
        _require_enabled(settings)
        _definition(settings, payload.default_pipeline_key)
        try:
            workspace = job_db.create_workspace(
                payload.name,
                default_pipeline_key=payload.default_pipeline_key,
                cms_config=payload.cms_config,
                resource_config=payload.resource_config,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkspaceResponse(workspace=workspace)

    @router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def get_workspace(workspace_id: str) -> WorkspaceResponse:
        _require_enabled(settings)
        return WorkspaceResponse(workspace=_workspace_or_404(job_db, workspace_id))

    @router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
    def update_workspace(
        workspace_id: str,
        payload: WorkspaceUpdateRequest,
    ) -> WorkspaceResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        if payload.default_pipeline_key is not None:
            _definition(settings, payload.default_pipeline_key)
        try:
            workspace = job_db.update_workspace(
                workspace_id,
                name=payload.name,
                default_pipeline_key=payload.default_pipeline_key,
                cms_config=payload.cms_config,
                resource_config=payload.resource_config,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkspaceResponse(workspace=workspace)

    @router.get("/workspaces/{workspace_id}/stats", response_model=WorkspaceStatsResponse)
    def get_workspace_stats(workspace_id: str) -> WorkspaceStatsResponse:
        _require_enabled(settings)
        workspace = _workspace_or_404(job_db, workspace_id)
        pipeline_key = workspace.get("default_pipeline_key", "question_content")
        agents = agent_manager.get_all()
        busy = sum(1 for a in agents if a.busy)
        latest_run = job_db.get_latest_node_run_for_workspace(workspace_id)
        return WorkspaceStatsResponse(
            workspace_id=workspace_id,
            name=workspace.get("name", ""),
            pipeline_key=pipeline_key,
            pipeline_label=_pipeline_label(settings, pipeline_key),
            job_stats=job_db.count_jobs_by_status(workspace_id),
            agent_status={
                "total": len(agents),
                "busy": busy,
                "idle": len(agents) - busy,
            },
            latest_run=dict(latest_run) if latest_run else None,
        )

    @router.delete("/workspaces/{workspace_id}", response_model=DeleteWorkspaceResponse)
    def delete_workspace(workspace_id: str) -> DeleteWorkspaceResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        try:
            job_db.delete_workspace(workspace_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return DeleteWorkspaceResponse(deleted=workspace_id)

    @router.post("/workspaces/{workspace_id}/job-batches", response_model=JobBatchResponse)
    def create_workspace_job_batch(
        workspace_id: str,
        payload: JobBatchRequest,
    ) -> JobBatchResponse:
        return create_batch_for_workspace(workspace_id, payload)

    @router.get("/workspaces/{workspace_id}/jobs", response_model=JobsResponse)
    def list_workspace_jobs(
        workspace_id: str,
        pipeline_key: str | None = None,
        status: str | None = None,
    ) -> JobsResponse:
        _require_enabled(settings)
        _workspace_or_404(job_db, workspace_id)
        return JobsResponse(
            jobs=job_db.list_jobs(
                workspace_id=workspace_id,
                pipeline_key=pipeline_key,
                status=status,
            )
        )

    @router.post("/job-batches", response_model=JobBatchResponse)
    def create_job_batch(payload: JobBatchRequest) -> JobBatchResponse:
        return create_batch_for_workspace("default", payload)

    @router.get("/jobs", response_model=JobsResponse)
    def list_jobs(pipeline_key: str | None = None, status: str | None = None) -> JobsResponse:
        _require_enabled(settings)
        return JobsResponse(
            jobs=job_db.list_jobs(
                workspace_id="default",
                pipeline_key=pipeline_key,
                status=status,
            )
        )

    @router.get("/jobs/{job_id}", response_model=JobDetailResponse)
    def get_job(job_id: str) -> JobDetailResponse:
        _require_enabled(settings)
        job = job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return JobDetailResponse(
            job=job,
            nodes=job_db.list_job_nodes(job_id),
            runs=job_db.list_node_runs(job_id),
            artifacts=_artifact_names(job),
        )

    @router.get("/jobs/{job_id}/artifacts/{artifact_name:path}", response_model=ArtifactResponse)
    def get_artifact(job_id: str, artifact_name: str) -> ArtifactResponse:
        _require_enabled(settings)
        job = job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        path = _artifact_path(job, artifact_name)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        return ArtifactResponse(name=artifact_name, content=path.read_text(encoding="utf-8"))

    @router.post("/jobs/{job_id}/nodes/{node_key}/rerun", response_model=RerunNodeResponse)
    def rerun_node(job_id: str, node_key: str) -> RerunNodeResponse:
        _require_enabled(settings)
        job = job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        definition = _definition(settings, str(job["pipeline_key"]))
        if node_key not in definition.nodes:
            raise HTTPException(status_code=404, detail="Node not found")
        stale_nodes = downstream_nodes(definition, node_key)
        try:
            job_db.mark_node_for_rerun(job_id, node_key, stale_nodes)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RerunNodeResponse(job_id=job_id, node_key=node_key, stale_nodes=stale_nodes)

    @router.get("/jobs/{job_id}/{invalid_path:path}", response_model=ArtifactResponse)
    def reject_invalid_job_subpath(job_id: str, invalid_path: str) -> None:
        _require_enabled(settings)
        if job_db.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(status_code=400, detail="Invalid job path")

    return router
