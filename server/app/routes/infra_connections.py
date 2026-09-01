"""Admin read-only infrastructure connection surface (#335).

GET returns display-safe summaries of the instance database and object
store; POST ``.../test`` runs a live probe (``SELECT 1`` for the database,
``head_bucket`` for the store). Unlike the public ``/api/health`` endpoint —
which only ever exposes configured/reachable booleans — this admin-only
surface relays the failure reason (``TypeName: message``) so an operator can
act on it. Credentials themselves never leave the process.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from server.app.auth.dependencies import require_admin
from server.app.routes.infra_connections_contracts import (
    DatabaseConnectionView,
    InfraConnectionsResponse,
    InfraConnectionTestRequest,
    InfraConnectionTestResponse,
    StorageConnectionView,
)
from server.app.services.infra_connections import describe_database, describe_storage
from server.app.storage.probe import cached_storage_status, probe_settings
from server.app.storage.s3_settings import load_s3_settings


def _probe_database(job_db) -> str | None:
    """SELECT 1 through the facade; None when reachable, else the reason."""
    try:
        with job_db.read() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # a probe must never propagate
        # #204 broad-except audit: mirrors storage.probe.probe_settings — the
        # contract is "return a reason string, never raise": an admin testing
        # connectivity must get the verdict, not a 500, whatever the driver
        # raised (OperationalError, InterfaceError, pool timeouts, ... — no
        # narrow enumerable family). The "TypeName: message" conversion IS
        # the preservation: type and text ride the admin-facing response.
        return f"{type(exc).__name__}: {exc}"
    return None


def create_infra_connections_router(job_db) -> APIRouter:
    """Global admin endpoints (not workspace-scoped): require_admin per route."""
    router = APIRouter()

    @router.get(
        "/admin/infra-connections",
        response_model=InfraConnectionsResponse,
    )
    def get_infra_connections(
        request: Request,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> InfraConnectionsResponse:
        database = describe_database(job_db.dsn_identity)
        # Display reachability rides the shared health cache (5s TTL), so
        # this endpoint adds no probe traffic beyond what /api/health pays.
        reachable = cached_storage_status(request.app.state)["reachable"]
        storage = describe_storage(load_s3_settings(), reachable=reachable)
        return InfraConnectionsResponse(
            database=DatabaseConnectionView(**asdict(database)),
            storage=StorageConnectionView(**asdict(storage)),
        )

    @router.post(
        "/admin/infra-connections/test",
        response_model=InfraConnectionTestResponse,
    )
    def test_infra_connection(
        payload: InfraConnectionTestRequest,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> InfraConnectionTestResponse:
        if payload.target == "database":
            reason = _probe_database(job_db)
        else:
            settings = load_s3_settings()
            reason = (
                "storage not configured (AGENT_LEGION_S3_BUCKET unset)"
                if settings is None
                else probe_settings(settings)
            )
        return InfraConnectionTestResponse(target=payload.target, ok=reason is None, reason=reason)

    return router
