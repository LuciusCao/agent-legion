"""App-level exception handlers for the job HTTP surface.

Registered once at app assembly (``main.create_app``) so routes let service
errors escape instead of hand-writing ``try/except`` mapping blocks.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import Response

from server.app.routes.job_http import raise_job_http_error
from server.app.services.job_errors import JobServiceError


async def job_service_error_response(request: Request, error: Exception) -> Response:
    """App-level mapping for a ``JobServiceError`` escaping a route (#927).

    Replaces the per-route ``except JobServiceError: raise_job_http_error``
    boilerplate with the very same mapping: the error is translated by
    ``raise_job_http_error`` and the resulting ``HTTPException`` is rendered
    by FastAPI's stock handler, so status/detail/headers stay byte-identical
    to the old route-level ``raise HTTPException``. Subclasses the mapping
    does not know are re-raised unchanged (-> 500), as before. Routes with a
    bespoke mapping (materials 503/422/409, runs' storage 503, approvals'
    ``JobOperationError``) keep their own ``except`` ahead of this handler.
    """
    if not isinstance(error, JobServiceError):
        raise error
    try:
        raise_job_http_error(error)
    except HTTPException as http_error:
        return await http_exception_handler(request, http_error)


def register_job_http_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(JobServiceError, job_service_error_response)
