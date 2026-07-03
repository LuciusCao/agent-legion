from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse

from server.app.jobs import JobQueries
from server.app.routes.job_http import require_workflows_enabled
from server.app.routes.video_jobs_common import video_job_or_404
from server.app.settings import Settings
from server.app.storage_paths import resolve_job_dir
from server.app.video_capabilities._video_paths import build_video_source_response


def create_video_job_source_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/jobs/{job_id}/video/source",
        response_class=FileResponse,
        response_model=None,
        responses={200: {"content": {"video/mp4": {}}}, 302: {"description": "Redirect"}},
    )
    def get_video_job_source(job_id: str) -> FileResponse | RedirectResponse:
        require_workflows_enabled(settings)
        job = video_job_or_404(job_db, job_id)
        return build_video_source_response(resolve_job_dir(job, settings.jobs_dir), settings)

    return router
