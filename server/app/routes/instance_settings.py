from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from server.app.auth.dependencies import require_admin
from server.app.routes.instance_settings_contracts import (
    InstanceSettingsResponse,
    InstanceSettingsUpdate,
)
from server.app.services.instance_settings import effective_instance_document
from server.app.services.instance_settings_store import InstanceSettingsStore
from server.app.settings import Settings


def create_instance_settings_router(job_queries, settings: Settings) -> APIRouter:
    """Admin endpoints managing the instance-level settings document.

    Values are hydrated into Settings at startup; edits take effect on
    restart (no runtime hot-reload).
    """
    router = APIRouter()
    store = InstanceSettingsStore(job_queries)

    @router.get(
        "/admin/instance-settings",
        response_model=InstanceSettingsResponse,
    )
    def get_instance_settings(
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> InstanceSettingsResponse:
        return InstanceSettingsResponse.model_validate(effective_instance_document(store.get()))

    @router.put(
        "/admin/instance-settings",
        response_model=InstanceSettingsResponse,
    )
    def put_instance_settings(
        payload: InstanceSettingsUpdate,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> InstanceSettingsResponse:
        store.put(payload.model_dump())
        return InstanceSettingsResponse.model_validate(payload.model_dump())

    return router
