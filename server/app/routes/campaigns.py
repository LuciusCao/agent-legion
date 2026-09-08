"""Workspace campaigns API (#532 PR-A, design §3.2).

Endpoints: create (JSON inline / multipart manifest), preview (dry-run),
list, detail, pause / resume / cancel. Every write route refuses
studio-agent scoped tokens (STUDIO-AGENT-001, the job_mutations precedent:
campaigns are operator bulk actions, not agent tools); reads ride the
secured() group's require_workspace_access (viewer reads, editor writes).
PR-A ships no feeder — created campaigns stay ``pending`` until PR-B's
feeder picks them up; the API surface is the reviewable intermediate state.
"""

from __future__ import annotations

from typing import Annotated, Never

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from server.app.auth.dependencies import reject_studio_agent_scope
from server.app.auth.workspace_access import require_workspace_access
from server.app.routes.campaign_contracts import (
    CampaignCreateRequest,
    CampaignCreateResponse,
    CampaignDetailResponse,
    CampaignListResponse,
    CampaignPreviewRequest,
    CampaignPreviewResponse,
    CampaignStatusChangeResponse,
)
from server.app.routes.job_http import raise_job_http_error
from server.app.services.campaign_manifest import ManifestError
from server.app.services.campaign_service import (
    CampaignManifestTooLargeError,
    CampaignService,
    CampaignStorageUnavailableError,
)
from server.app.services.job_errors import JobServiceError

_LIST_LIMIT_MAX = 200


async def _read_upload_size(manifest: UploadFile, size: int) -> bytes:
    """UploadFile.read seam — tests pin the size argument (the OOM bound)."""
    return await manifest.read(size)


def _raise_campaign_http_error(error: JobServiceError) -> Never:
    if isinstance(error, CampaignStorageUnavailableError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    if isinstance(error, CampaignManifestTooLargeError):
        raise HTTPException(status_code=413, detail=str(error)) from error
    if isinstance(error, ManifestError):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise_job_http_error(error)


def create_campaigns_router(service: CampaignService) -> APIRouter:
    # The whole router is the mutation surface: reads (list/detail) share the
    # guard for uniformity — campaigns are operator tooling, not an agent
    # tool face (STUDIO-AGENT-001).
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])

    def _create_response(body: dict) -> CampaignCreateResponse:
        return CampaignCreateResponse.model_validate({"campaign": body})

    @router.post(
        "/workspaces/{workspace_id}/campaigns",
        response_model=CampaignCreateResponse,
    )
    def create_campaign(
        workspace_id: str,
        payload: CampaignCreateRequest,
        user: Annotated[dict, Depends(require_workspace_access)],
    ) -> CampaignCreateResponse:
        created_by = str(user.get("id") or "")
        try:
            if (submit := payload.submit) is not None:
                body = service.create_campaign(
                    workspace_id,
                    "submit",
                    items=[item.model_dump() for item in submit.items],
                    created_by=created_by,
                    watermark=submit.watermark,
                    batch_size=submit.batch_size,
                )
            elif (rerun := payload.rerun) is not None:
                body = service.create_campaign(
                    workspace_id,
                    payload.mode,
                    job_ids=rerun.job_ids,
                    job_filter=rerun.resolved_filter(),
                    node_key=rerun.node_key,
                    from_failed_node=rerun.from_failed_node,
                    created_by=created_by,
                    watermark=rerun.watermark,
                    batch_size=rerun.batch_size,
                )
            else:  # pragma: no cover - the contract validator guarantees one
                raise HTTPException(status_code=422, detail="missing target block")
        except JobServiceError as exc:
            _raise_campaign_http_error(exc)
        return _create_response(body)

    @router.post(
        "/workspaces/{workspace_id}/campaigns/upload",
        response_model=CampaignCreateResponse,
    )
    async def create_campaign_from_manifest(
        workspace_id: str,
        user: Annotated[dict, Depends(require_workspace_access)],
        mode: Annotated[str, Form()] = "submit",
        watermark: Annotated[int | None, Form()] = None,
        batch_size: Annotated[int | None, Form()] = None,
        manifest: Annotated[UploadFile, File()] = None,  # type: ignore[assignment]
    ) -> CampaignCreateResponse:
        """Multipart variant: manifest file (.jsonl / .csv) + form knobs.

        The submit channel beyond the inline ceiling; the read is bounded
        by the 50MB cap (ceiling+1 bytes max, oversized → 413) and
        normalized server-side before any row exists.
        """
        if mode != "submit":
            raise HTTPException(
                status_code=422,
                detail="The upload channel is submit-mode only; use the JSON body"
                " for rerun/upgrade campaigns",
            )
        if manifest is None:
            raise HTTPException(
                status_code=422, detail="A manifest file is required (field 'manifest')"
            )
        # Bounded read (PR #541 P1): at most manifest_max_bytes+1 enter memory
        # (+1 distinguishes "over the ceiling" from "exactly at it"); an
        # oversized upload is refused 413 without reading the rest.
        limit = service.manifest_max_bytes + 1
        data = await _read_upload_size(manifest, limit)
        if len(data) > service.manifest_max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Manifest exceeds the {service.manifest_max_bytes} byte limit",
            )
        try:
            body = service.create_campaign(
                workspace_id,
                "submit",
                manifest_filename=manifest.filename or "manifest.jsonl",
                manifest_bytes=data,
                created_by=str(user.get("id") or ""),
                watermark=watermark,
                batch_size=batch_size,
            )
        except JobServiceError as exc:
            _raise_campaign_http_error(exc)
        return _create_response(body)

    @router.post(
        "/workspaces/{workspace_id}/campaigns/preview",
        response_model=CampaignPreviewResponse,
    )
    def preview_campaign(
        workspace_id: str, payload: CampaignPreviewRequest
    ) -> CampaignPreviewResponse:
        try:
            if (submit := payload.submit) is not None:
                result = service.preview_campaign(
                    workspace_id,
                    "submit",
                    items=[item.model_dump() for item in submit.items],
                    watermark=submit.watermark,
                    batch_size=submit.batch_size,
                )
            elif (rerun := payload.rerun) is not None:
                result = service.preview_campaign(
                    workspace_id,
                    payload.mode,
                    job_ids=rerun.job_ids,
                    job_filter=rerun.resolved_filter(),
                    node_key=rerun.node_key,
                    from_failed_node=rerun.from_failed_node,
                    watermark=rerun.watermark,
                    batch_size=rerun.batch_size,
                )
            else:  # pragma: no cover - the contract validator guarantees one
                raise HTTPException(status_code=422, detail="missing target block")
        except JobServiceError as exc:
            _raise_campaign_http_error(exc)
        return CampaignPreviewResponse.model_validate({"result": result})

    @router.get(
        "/workspaces/{workspace_id}/campaigns",
        response_model=CampaignListResponse,
    )
    def list_campaigns(
        workspace_id: str,
        limit: Annotated[int, Query(ge=1, le=_LIST_LIMIT_MAX)] = 50,
    ) -> CampaignListResponse:
        return CampaignListResponse.model_validate(
            {"campaigns": service.list_campaigns(workspace_id, limit=limit)}
        )

    @router.get(
        "/workspaces/{workspace_id}/campaigns/{campaign_id}",
        response_model=CampaignDetailResponse,
    )
    def get_campaign(workspace_id: str, campaign_id: str) -> CampaignDetailResponse:
        try:
            return CampaignDetailResponse.model_validate(
                {"campaign": service.get_campaign(workspace_id, campaign_id)}
            )
        except JobServiceError as exc:
            _raise_campaign_http_error(exc)

    @router.post(
        "/workspaces/{workspace_id}/campaigns/{campaign_id}/pause",
        response_model=CampaignStatusChangeResponse,
    )
    def pause_campaign(workspace_id: str, campaign_id: str) -> CampaignStatusChangeResponse:
        try:
            return CampaignStatusChangeResponse.model_validate(
                {"campaign": service.pause_campaign(workspace_id, campaign_id)}
            )
        except JobServiceError as exc:
            _raise_campaign_http_error(exc)

    @router.post(
        "/workspaces/{workspace_id}/campaigns/{campaign_id}/resume",
        response_model=CampaignStatusChangeResponse,
    )
    def resume_campaign(workspace_id: str, campaign_id: str) -> CampaignStatusChangeResponse:
        try:
            return CampaignStatusChangeResponse.model_validate(
                {"campaign": service.resume_campaign(workspace_id, campaign_id)}
            )
        except JobServiceError as exc:
            _raise_campaign_http_error(exc)

    @router.post(
        "/workspaces/{workspace_id}/campaigns/{campaign_id}/cancel",
        response_model=CampaignStatusChangeResponse,
    )
    def cancel_campaign(workspace_id: str, campaign_id: str) -> CampaignStatusChangeResponse:
        try:
            return CampaignStatusChangeResponse.model_validate(
                {"campaign": service.cancel_campaign(workspace_id, campaign_id)}
            )
        except JobServiceError as exc:
            _raise_campaign_http_error(exc)

    return router
