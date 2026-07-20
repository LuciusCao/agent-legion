"""Shared worker-token authorization for remote-facing routes.

Two credential shapes are accepted on worker-facing endpoints:

- per-worker tokens (``{worker_id}.{secret}``, sha256-hashed in the
  ``remote_workers`` table, revocable) — the SEC-WORKER-001 baseline;
- the global static ``remote.worker_token`` — only while
  ``remote.allow_legacy_worker_token`` keeps the migration window open, and
  never for a revoked worker (revocation beats the window).

Management endpoints (token issuance / revocation) accept only the global
static token, acting as the management credential; see ``routes/remote.py``.
"""

from __future__ import annotations

import hmac
from typing import Any, Protocol

from fastapi import HTTPException, Request

from server.app.executors.runtime_config import RemoteRuntimeConfig


class WorkerAuthenticator(Protocol):
    """The broker surface the authorizer needs (kept structural for testability)."""

    def authenticate_worker(self, token: str) -> dict[str, Any] | None: ...

    def is_worker_revoked(self, worker_id: str) -> bool: ...


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
    allow_legacy: bool,
    expected_worker_id: str | None = None,
    require_worker_id: bool = True,
) -> dict[str, Any]:
    """Authenticate a worker-facing request; returns ``{"worker_id": ...}``.

    A valid per-worker token binds the worker_id it was issued for (the
    X-Worker-Id header is ignored in that case). Failing that, the legacy
    global token is accepted during the fallback window, identifying the
    worker by its X-Worker-Id header — except revoked workers, which are
    rejected even with a correct legacy token.
    """
    if not remote_config.worker_token:
        raise HTTPException(status_code=503, detail="remote execution is not enabled")
    token = request.headers.get("x-worker-token", "")
    worker_id = ""
    record = broker.authenticate_worker(token) if broker is not None and token else None
    if record is not None:
        worker_id = str(record["worker_id"])
    elif allow_legacy and hmac.compare_digest(token, remote_config.worker_token):
        worker_id = request.headers.get("x-worker-id", "")
        if worker_id and broker is not None and broker.is_worker_revoked(worker_id):
            raise HTTPException(status_code=401, detail="worker is revoked")
    else:
        raise HTTPException(status_code=401, detail="invalid worker token")
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
            allow_legacy=remote_config.allow_legacy_worker_token,
            expected_worker_id=expected_worker_id,
            require_worker_id=require_worker_id,
        )

    return authorize
