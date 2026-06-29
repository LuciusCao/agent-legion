from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from server.app.jobs import JobQueries
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.video_jobs_common import video_job_or_404
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir


def create_video_job_source_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/jobs/{job_id}/video/source",
        response_class=FileResponse,
        responses={200: {"content": {"video/mp4": {}}}},
    )
    def get_video_job_source(job_id: str) -> FileResponse:
        require_workflows_enabled(settings)
        job = video_job_or_404(job_db, job_id)
        source_path = resolve_job_dir(job, settings.jobs_dir) / "source.mp4"
        if not source_path.is_file():
            raise HTTPException(status_code=404, detail="Video source not found")
        return FileResponse(source_path, media_type="video/mp4", filename="source.mp4")

    return router
