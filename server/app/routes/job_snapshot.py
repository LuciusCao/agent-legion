from __future__ import annotations

from fastapi import APIRouter

from server.app.routes.job_list import create_job_list_router
from server.app.services.job_list_queries import JobListQueryService
from server.app.services.job_patch_queries import JobPatchQueryService
from server.app.settings import Settings


def create_job_snapshot_router(
    job_patch_queries: JobPatchQueryService,
    settings: Settings,
) -> APIRouter:
    # The snapshot/facets endpoints are served by the job list router; reuse
    # the patch query service's dependencies to build it.
    job_list_queries = JobListQueryService(
        job_patch_queries.job_db,
        settings,
        job_event_buffer=job_patch_queries._job_event_buffer,
    )
    return create_job_list_router(job_list_queries, settings)
