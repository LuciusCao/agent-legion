from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

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
    restart (no runtime hot-reload) — except the keys read at use time
    (materials / execution retention, the #989 CSP compatibility switch).
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
        # #786: the loaded (env-applied) runtime is the default source, so a
        # legacy document without node_code_max_bytes shows the env value the
        # instance actually runs with (instance setting > env > 64KB).
        return InstanceSettingsResponse.model_validate(
            effective_instance_document(store.get(), settings.executor_runtime)
        )

    @router.put(
        "/admin/instance-settings",
        response_model=InstanceSettingsResponse,
    )
    def put_instance_settings(
        payload: InstanceSettingsUpdate,
        request: Request,
        _admin: Annotated[dict[str, Any], Depends(require_admin)],
    ) -> InstanceSettingsResponse:
        store.put(payload.model_dump())
        # #989: the document CSP switch is read at serve time through a
        # short cache; drop it so the next page load sees the new value.
        csp_compat = getattr(request.app.state, "csp_compat", None)
        if csp_compat is not None:
            csp_compat.invalidate()
        return InstanceSettingsResponse.model_validate(payload.model_dump())

    return router
