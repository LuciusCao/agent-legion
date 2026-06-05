from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.app.jobs import JobQueries
from server.app.pipelines.definition import load_pipeline_definition
from server.app.pipelines.scheduler import downstream_nodes
from server.app.settings import Settings


class JobBatchRequest(BaseModel):
    pipeline_key: str
    source_kind: str
    question_ids: list[str] = Field(default_factory=list)
    knowledge_codes: list[str] = Field(default_factory=list)


class JobBatchResponse(BaseModel):
    batch: dict[str, Any]
    created_count: int
    jobs: list[dict[str, Any]]


class JobsResponse(BaseModel):
    jobs: list[dict[str, Any]]


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


def _pipelines_enabled(settings: Settings) -> bool:
    pipelines = settings.config.get("pipelines", {})
    return isinstance(pipelines, dict) and bool(pipelines.get("enabled"))


def _require_enabled(settings: Settings) -> None:
    if not _pipelines_enabled(settings):
        raise HTTPException(status_code=404, detail="Pipelines are disabled")


def _definition(settings: Settings, pipeline_key: str):
    if pipeline_key != "question_content":
        raise HTTPException(status_code=404, detail="Unknown pipeline")
    path = settings.root_dir / "config" / "pipelines" / "question_content.yaml"
    return load_pipeline_definition(path)


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


def create_jobs_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post("/job-batches", response_model=JobBatchResponse)
    def create_job_batch(payload: JobBatchRequest) -> JobBatchResponse:
        _require_enabled(settings)
        definition = _definition(settings, payload.pipeline_key)
        source_payload = payload.model_dump()
        batch = job_db.create_batch(payload.pipeline_key, payload.source_kind, source_payload)
        question_ids = list(dict.fromkeys(q.strip() for q in payload.question_ids if q.strip()))
        jobs: list[dict[str, Any]] = []
        for question_id in question_ids:
            jobs.append(
                job_db.create_job(
                    pipeline_key=payload.pipeline_key,
                    source_type="question_id",
                    source_id=question_id,
                    batch_id=batch["id"],
                    title=f"Question {question_id}",
                    node_keys=list(definition.nodes),
                )
            )

        batch["created_count"] = len(jobs)
        return JobBatchResponse(batch=batch, created_count=len(jobs), jobs=jobs)

    @router.get("/jobs", response_model=JobsResponse)
    def list_jobs(pipeline_key: str | None = None, status: str | None = None) -> JobsResponse:
        _require_enabled(settings)
        return JobsResponse(jobs=job_db.list_jobs(pipeline_key=pipeline_key, status=status))

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
        job_db.mark_node_for_rerun(job_id, node_key, stale_nodes)
        return RerunNodeResponse(job_id=job_id, node_key=node_key, stale_nodes=stale_nodes)

    @router.get("/jobs/{job_id}/{invalid_path:path}", response_model=ArtifactResponse)
    def reject_invalid_job_subpath(job_id: str, invalid_path: str) -> None:
        _require_enabled(settings)
        if job_db.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(status_code=400, detail="Invalid job path")

    return router
