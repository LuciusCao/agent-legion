from typing import Never

from fastapi import HTTPException

from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    JobServiceError,
    NotFoundError,
    UnsupportedOperationError,
)
from server.app.settings import Settings


def require_pipelines_enabled(settings: Settings) -> None:
    pipelines = settings.config.get("pipelines", {})
    if not isinstance(pipelines, dict) or not pipelines.get("enabled"):
        raise HTTPException(status_code=404, detail="Pipelines are disabled")


def raise_job_http_error(error: JobServiceError) -> Never:
    if isinstance(error, NotFoundError):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, UnsupportedOperationError):
        raise HTTPException(status_code=501, detail=str(error)) from error
    if isinstance(error, (InvalidOperationError, ConflictError)):
        raise HTTPException(status_code=400, detail=str(error)) from error
    raise error
