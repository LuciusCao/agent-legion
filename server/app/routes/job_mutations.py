"""Effecting job mutations: delete, rerun, run-to, continue and batch intake.

Every endpoint here takes effect immediately, so the whole router refuses
studio-agent scoped tokens (STUDIO-AGENT-001); job reads live in jobs.py.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from server.app.auth.dependencies import reject_studio_agent_scope, require_user
from server.app.routes.job_batch_operations import ACCEPTED_202, async_dispatch_or_none
from server.app.routes.job_contracts import (
    DeleteJobResponse,
)
from server.app.routes.job_http import (
    raise_job_operation_error,
)
from server.app.routes.job_operation_contracts import (
    BatchJobIdsRequest,
    BatchJobMutationResponse,
    BatchRunToRequest,
    ContinueJobRequest,
    JobBatchRerunRequest,
    JobMutationResultResponse,
    RunToRequest,
)
from server.app.services.job_deletion import JobDeletionService
from server.app.services.job_execution import JobExecutionService
from server.app.services.job_operation_error import JobOperationError
from server.app.services.job_queries import JobQueryService
from server.app.services.job_rerun import JobRerunService


def create_job_mutations_router(
    job_queries: JobQueryService,
    job_rerun: JobRerunService,
    job_deletion: JobDeletionService,
    job_execution: JobExecutionService,
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(reject_studio_agent_scope)])
    job_db = job_queries.job_db

    def _mutation_response(results: list) -> BatchJobMutationResponse:
        return BatchJobMutationResponse(
            results=[JobMutationResultResponse.model_validate(result) for result in results]
        )

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-rerun",
        response_model=BatchJobMutationResponse,
        responses=ACCEPTED_202,
    )
    def batch_rerun_workspace_jobs(
        workspace_id: str,
        payload: JobBatchRerunRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> BatchJobMutationResponse | JSONResponse:
        # #946: above the sync threshold the selection enqueues (202 + handle).
        ids, accepted = async_dispatch_or_none(
            job_db,
            workspace_id,
            "rerun",
            payload,
            {"node_key": payload.node_key, "from_failed_node": payload.from_failed_node},
            user,
        )
        if accepted is not None:
            return accepted
        return _mutation_response(
            job_rerun.batch_rerun(
                workspace_id, ids, payload.node_key, from_failed_node=payload.from_failed_node
            )
        )

    @router.delete(
        "/workspaces/{workspace_id}/jobs/batch",
        response_model=BatchJobMutationResponse,
        responses=ACCEPTED_202,
    )
    def batch_delete_workspace_jobs(
        workspace_id: str,
        payload: BatchJobIdsRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> BatchJobMutationResponse | JSONResponse:
        ids, accepted = async_dispatch_or_none(job_db, workspace_id, "delete", payload, {}, user)
        if accepted is not None:
            return accepted
        return _mutation_response(job_deletion.batch_delete(workspace_id, ids))

    @router.post("/jobs/{job_id}/nodes/{node_key}/rerun", response_model=JobMutationResultResponse)
    def rerun_node(job_id: str, node_key: str) -> JobMutationResultResponse:
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            result = job_rerun.rerun(job["workspace_id"], job_id, node_key)
        except JobOperationError as exc:
            raise_job_operation_error(exc)
        return JobMutationResultResponse.model_validate(result)

    @router.delete("/jobs/{job_id}", response_model=DeleteJobResponse)
    def delete_job(job_id: str) -> DeleteJobResponse:
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            job_deletion.delete(job["workspace_id"], job_id)
        except JobOperationError as exc:
            raise_job_operation_error(exc)
        return DeleteJobResponse(deleted=job_id)

    @router.post("/jobs/{job_id}/run-to", response_model=JobMutationResultResponse)
    def run_to(job_id: str, payload: RunToRequest) -> JobMutationResultResponse:
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            result = job_execution.run_to(
                job["workspace_id"],
                job_id,
                payload.target_node_key,
                payload.start_node_key,
            )
        except JobOperationError as exc:
            raise_job_operation_error(exc)
        return JobMutationResultResponse.model_validate(result)

    @router.post(
        "/jobs/{job_id}/continue",
        response_model=JobMutationResultResponse,
    )
    def continue_job(
        job_id: str,
        payload: ContinueJobRequest,
    ) -> JobMutationResultResponse:
        job = job_queries.job_db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            result = job_execution.continue_job(job["workspace_id"], job_id)
        except JobOperationError as exc:
            raise_job_operation_error(exc)
        return JobMutationResultResponse.model_validate(result)

    @router.post(
        "/workspaces/{workspace_id}/jobs/batch-run-to",
        response_model=BatchJobMutationResponse,
        responses=ACCEPTED_202,
    )
    def batch_run_to(
        workspace_id: str,
        payload: BatchRunToRequest,
        user: Annotated[dict[str, Any], Depends(require_user)],
    ) -> BatchJobMutationResponse | JSONResponse:
        ids, accepted = async_dispatch_or_none(
            job_db,
            workspace_id,
            "run_to",
            payload,
            {"target_node_key": payload.target_node_key, "start_node_key": payload.start_node_key},
            user,
        )
        if accepted is not None:
            return accepted
        return _mutation_response(
            job_execution.batch_run_to(
                workspace_id, ids, payload.target_node_key, payload.start_node_key
            )
        )

    return router
