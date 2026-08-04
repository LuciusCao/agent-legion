from __future__ import annotations

from fastapi import APIRouter

from server.app.jobs import JobQueries
from server.app.routes.video_jobs_detail import create_video_job_detail_router
from server.app.routes.video_jobs_source import create_video_job_source_router
from server.app.settings import Settings


def create_video_jobs_router(job_db: JobQueries, settings: Settings) -> APIRouter:
    router = APIRouter()
    router.include_router(create_video_job_source_router(job_db, settings))
    router.include_router(create_video_job_detail_router(job_db, settings))
    return router
