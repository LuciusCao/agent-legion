"""Per-worker token authorization for remote-facing routes."""

from __future__ import annotations

from typing import Any, Protocol

from fastapi import HTTPException, Request

from server.app.executors.runtime_config import RemoteRuntimeConfig


class WorkerAuthenticator(Protocol):
    """The broker surface the authorizer needs (kept structural for testability)."""

    def authenticate_worker(self, token: str) -> dict[str, Any] | None: ...


class WorkerAuthorizer(Protocol):
    def __call__(
        self,
        request: Request,
        expected_worker_id: str | None = None,
        require_worker_id: bool = True,
    ) -> dict[str, Any]: ...


def authorize_worker(
    request: Request,
    broker: WorkerAuthenticator | None,
    remote_config: RemoteRuntimeConfig,
    *,
    expected_worker_id: str | None = None,
    require_worker_id: bool = True,
) -> dict[str, Any]:
    """Authenticate a worker-facing request; returns ``{"worker_id": ...}``.

    Only revocable per-worker tokens are accepted. The static management
    token never authenticates worker-facing endpoints.
    """
    if not remote_config.worker_token:
        raise HTTPException(status_code=503, detail="remote execution is not enabled")
    token = request.headers.get("x-worker-token", "")
    record = broker.authenticate_worker(token) if broker is not None and token else None
    if record is None:
        raise HTTPException(status_code=401, detail="invalid worker token")
    worker_id = str(record["worker_id"])
    if require_worker_id and not worker_id:
        raise HTTPException(status_code=400, detail="missing X-Worker-Id header")
    if expected_worker_id is not None and worker_id != expected_worker_id:
        raise HTTPException(status_code=400, detail="worker id mismatch")
    return {"worker_id": worker_id}


def create_worker_authorizer(
    remote_config: RemoteRuntimeConfig, broker: WorkerAuthenticator | None = None
) -> WorkerAuthorizer:
    def authorize(
        request: Request, expected_worker_id: str | None = None, require_worker_id: bool = True
    ) -> dict[str, Any]:
        return authorize_worker(
            request,
            broker,
            remote_config,
            expected_worker_id=expected_worker_id,
            require_worker_id=require_worker_id,
        )

    return authorize
