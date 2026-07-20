"""Shared worker-token authorization for remote-facing routes.

Extracted verbatim from ``routes/remote.py`` so additional worker-facing
routers (e.g. artifacts) enforce identical token semantics.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import HTTPException, Request

from server.app.executors.runtime_config import RemoteRuntimeConfig


class WorkerAuthorizer(Protocol):
    def __call__(
        self,
        request: Request,
        expected_worker_id: str | None = None,
        require_worker_id: bool = True,
    ) -> str: ...


def create_worker_authorizer(remote_config: RemoteRuntimeConfig) -> WorkerAuthorizer:
    def authorize(
        request: Request, expected_worker_id: str | None = None, require_worker_id: bool = True
    ) -> str:
        if not remote_config.worker_token:
            raise HTTPException(status_code=503, detail="remote execution is not enabled")
        if request.headers.get("x-worker-token") != remote_config.worker_token:
            raise HTTPException(status_code=401, detail="invalid worker token")
        worker_id = request.headers.get("x-worker-id", "")
        if require_worker_id and not worker_id:
            raise HTTPException(status_code=400, detail="missing X-Worker-Id header")
        if expected_worker_id is not None and worker_id != expected_worker_id:
            raise HTTPException(status_code=400, detail="worker id mismatch")
        return worker_id

    return authorize
