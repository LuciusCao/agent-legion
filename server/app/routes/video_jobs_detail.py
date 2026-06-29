from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.app.jobs import JobQueries
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.video_jobs_common import video_job_or_404
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.video_capabilities.projection import project_video_job_detail
from server.app.video_capabilities.response_contracts import VideoJobDetailResponse


def create_video_job_detail_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/jobs/{job_id}/video", response_model=VideoJobDetailResponse)
    def get_video_job_detail(job_id: str) -> VideoJobDetailResponse:
        require_workflows_enabled(settings)
        job = video_job_or_404(job_db, job_id)
        job_dir = resolve_job_dir(job, settings.jobs_dir)
        try:
            return project_video_job_detail(
                job_dir, local_video_url=f"/api/jobs/{job_id}/video/source"
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
