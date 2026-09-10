"""Workspace campaigns API (#532, design §3.2).

Endpoints: create (JSON inline / multipart manifest), preview (dry-run),
list, detail, pause / resume / cancel. Every write route refuses
studio-agent scoped tokens (STUDIO-AGENT-001, the job_mutations precedent:
campaigns are operator bulk actions, not agent tools); reads ride the
secured() group's require_workspace_access (viewer reads, editor writes).
The PR-B feeder (workflow_worker/campaign_feeder.py) drains active rows;
resume additionally pokes it via app.state so the next batch is immediate.
"""

from __future__ import annotations

from typing import Annotated, Never

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

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


def _raise_campaign_http_error(error: JobServiceError | ManifestError) -> Never:
    if isinstance(error, CampaignStorageUnavailableError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    if isinstance(error, CampaignManifestTooLargeError):
        raise HTTPException(status_code=413, detail=str(error)) from error
    if isinstance(error, ManifestError):
        # 文件清单的合同错（二轮 P2：service 原样穿透到此）——操作员可
        # 修复，按 422 映射。
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise_job_http_error(error)


def create_campaigns_router(service: CampaignService) -> APIRouter:
    # The whole router is the mutation surface: reads (list/detail) share the
    # guard for uniformity — campaigns are operator tooling, not an agent
    # tool face (STUDIO-AGENT-001). The JSON-body byte ceiling rides the app
    # -level CampaignBodyLimitMiddleware (routes/campaign_body_limit.py,
    # mounted in main.py: APIRouter has no middleware surface of its own).
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
                    name=payload.name,
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
                    exclude_ids=rerun.exclude_ids,
                    created_by=created_by,
                    name=payload.name,
                    watermark=rerun.watermark,
                    batch_size=rerun.batch_size,
                )
            else:  # pragma: no cover - the contract validator guarantees one
                raise HTTPException(status_code=422, detail="missing target block")
        except (JobServiceError, ManifestError) as exc:
            _raise_campaign_http_error(exc)
        return _create_response(body)

    @router.post(
        "/workspaces/{workspace_id}/campaigns/upload",
        response_model=CampaignCreateResponse,
    )
    async def create_campaign_from_manifest(
        workspace_id: str,
        user: Annotated[dict, Depends(require_workspace_access)],
        # 四轮 P2（F3）：manifest 是合同必填项——由 FastAPI 的 File() 声明
        # （缺失 422 由框架产生），OpenAPI/生成的 api.ts 不再把它标成
        # optional；无默认值参数必须排在带默认值的表单字段之前。
        manifest: Annotated[UploadFile, File()],
        mode: Annotated[str, Form()] = "submit",
        # 表单字段的长度界（审核 P3）：有界读只覆盖文件部分，name 会被
        # python-multipart 全量缓冲后再校验——Form 侧限长封住这个缺口。
        name: Annotated[str, Form(max_length=200)] = "",
        watermark: Annotated[int | None, Form()] = None,
        batch_size: Annotated[int | None, Form()] = None,
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
                name=name,
                watermark=watermark,
                batch_size=batch_size,
            )
        except (JobServiceError, ManifestError) as exc:
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
                    exclude_ids=rerun.exclude_ids,
                    watermark=rerun.watermark,
                    batch_size=rerun.batch_size,
                )
            else:  # pragma: no cover - the contract validator guarantees one
                raise HTTPException(status_code=422, detail="missing target block")
        except (JobServiceError, ManifestError) as exc:
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
        except (JobServiceError, ManifestError) as exc:
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
        except (JobServiceError, ManifestError) as exc:
            _raise_campaign_http_error(exc)

    @router.post(
        "/workspaces/{workspace_id}/campaigns/{campaign_id}/resume",
        response_model=CampaignStatusChangeResponse,
    )
    def resume_campaign(
        workspace_id: str,
        campaign_id: str,
        request: Request,
    ) -> CampaignStatusChangeResponse:
        try:
            campaign = service.resume_campaign(workspace_id, campaign_id)
        except (JobServiceError, ManifestError) as exc:
            _raise_campaign_http_error(exc)
        # Wake the feeder (design §2.4): the paused row left the active scan,
        # so without the poke the resumed campaign waits out the tick cadence
        # (worst case feeder_tick_seconds + the sleep phase). The feeder is
        # process-local app.state — absent on non-Host process shapes by
        # construction, and its wake never raises.
        feeder = getattr(request.app.state, "campaign_feeder", None)
        if feeder is not None:
            feeder.wake()
        return CampaignStatusChangeResponse.model_validate({"campaign": campaign})

    @router.post(
        "/workspaces/{workspace_id}/campaigns/{campaign_id}/cancel",
        response_model=CampaignStatusChangeResponse,
    )
    def cancel_campaign(workspace_id: str, campaign_id: str) -> CampaignStatusChangeResponse:
        try:
            return CampaignStatusChangeResponse.model_validate(
                {"campaign": service.cancel_campaign(workspace_id, campaign_id)}
            )
        except (JobServiceError, ManifestError) as exc:
            _raise_campaign_http_error(exc)

    return router
