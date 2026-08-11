from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import require_admin
from server.app.routes.connections_contracts import (
    ConnectionCreate,
    ConnectionListResponse,
    ConnectionTestResponse,
    ConnectionTypesResponse,
    ConnectionUpdate,
    ConnectionView,
)
from server.app.routes.job_http import raise_job_http_error
from server.app.services.connection_adapters import list_adapter_types
from server.app.services.connections import ConnectionService
from server.app.services.job_errors import JobServiceError
from server.app.settings import Settings


def create_connections_router(settings: Settings) -> APIRouter:
    """Admin endpoints managing instance-level external connections."""
    router = APIRouter()
    service = ConnectionService(settings.database_url, settings.config)

    @router.get("/admin/connections", response_model=ConnectionListResponse)
    def list_connections(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> ConnectionListResponse:
        return ConnectionListResponse(
            connections=[ConnectionView(**view) for view in service.list()]
        )

    @router.get("/admin/connection-types", response_model=ConnectionTypesResponse)
    def list_connection_types(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> ConnectionTypesResponse:
        return ConnectionTypesResponse(
            types=list_adapter_types()  # type: ignore[arg-type]
        )

    @router.post("/admin/connections", response_model=ConnectionView)
    def create_connection(
        payload: ConnectionCreate,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> ConnectionView:
        try:
            return ConnectionView(
                **service.create(payload.key, payload.type, payload.display_name, payload.config)
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.put("/admin/connections/{key}", response_model=ConnectionView)
    def update_connection(
        key: str,
        payload: ConnectionUpdate,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> ConnectionView:
        try:
            return ConnectionView(
                **service.update(
                    key,
                    display_name=payload.display_name,
                    config=payload.config,
                    enabled=payload.enabled,
                )
            )
        except JobServiceError as exc:
            raise_job_http_error(exc)

    @router.delete("/admin/connections/{key}", response_model=ConnectionTestResponse)
    def delete_connection(
        key: str,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> ConnectionTestResponse:
        try:
            service.delete(key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return ConnectionTestResponse(ok=True, message=f"connection {key} 已删除")

    @router.post("/admin/connections/{key}/test", response_model=ConnectionTestResponse)
    def test_connection(
        key: str,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> ConnectionTestResponse:
        try:
            result = service.probe(key)
        except JobServiceError as exc:
            raise_job_http_error(exc)
        return ConnectionTestResponse(ok=bool(result["ok"]), message=str(result["message"]))

    return router
