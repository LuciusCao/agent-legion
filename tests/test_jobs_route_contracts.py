from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from scripts import export_openapi
from scripts.export_openapi import build_openapi_schema
from server.app.jobs.storage_layout import job_shard
from server.app.routes.job_view_contracts import (
    ExecutionControlSummaryResponse,
    JobDetailResponse,
    JobNodeSummaryResponse,
    JobsResponse,
    JobSummaryResponse,
)

EXPECTED_OPERATIONS = {
    ("get", "/api/workflows"): "WorkflowsListResponse",
    ("post", "/api/workflows"): "WorkflowRegisteredResponse",
    ("get", "/api/workflows/{workflow_key}"): "WorkflowResponse",
    ("get", "/api/workspaces"): "WorkspacesResponse",
    ("post", "/api/workspaces"): "WorkspaceResponse",
    ("get", "/api/workspaces/{workspace_id}"): "WorkspaceResponse",
    ("patch", "/api/workspaces/{workspace_id}"): "WorkspaceResponse",
    ("delete", "/api/workspaces/{workspace_id}"): "DeleteWorkspaceResponse",
    ("get", "/api/workspaces/{workspace_id}/stats"): "WorkspaceStatsResponse",
    ("get", "/api/workspaces/{workspace_id}/settings"): "WorkspaceSettingsResponse",
    ("put", "/api/workspaces/{workspace_id}/configuration"): "WorkspaceConfigurationResponse",
    ("patch", "/api/workspaces/{workspace_id}/settings/{section}"): "WorkspaceSettingsResponse",
    ("post", "/api/workspaces/{workspace_id}/job-batches"): "JobBatchResponse",
    ("get", "/api/workspaces/{workspace_id}/jobs"): "JobsResponse",
    ("post", "/api/workspaces/{workspace_id}/jobs/batch-rerun"): "BatchJobMutationResponse",
    ("delete", "/api/workspaces/{workspace_id}/jobs/batch"): "BatchJobMutationResponse",
    ("post", "/api/workspaces/{workspace_id}/jobs/batch-run-to"): "BatchJobMutationResponse",
    ("get", "/api/workspaces/{workspace_id}/runs"): "WorkspaceRunsResponse",
    ("get", "/api/workspaces/{workspace_id}/dag"): "WorkspaceDagResponse",
    ("get", "/api/jobs/{job_id}"): "JobDetailResponse",
    ("delete", "/api/jobs/{job_id}"): "DeleteJobResponse",
    ("post", "/api/jobs/{job_id}/nodes/{node_key}/rerun"): "JobMutationResultResponse",
    ("post", "/api/jobs/{job_id}/run-to"): "JobMutationResultResponse",
    ("post", "/api/jobs/{job_id}/continue"): "JobMutationResultResponse",
    ("get", "/api/jobs/{job_id}/artifacts/{artifact_name}"): "ArtifactResponse",
    ("get", "/api/jobs/{job_id}/runs/{run_id}/log"): "JobLogResponse",
    ("get", "/api/jobs/{job_id}/runs/{run_id}/token-usage"): "TokenUsageRunResponse",
    ("get", "/api/jobs/{job_id}/token-usage"): "TokenUsageJobResponse",
    ("get", "/api/workspaces/{workspace_id}/token-usage"): "TokenUsageWorkspaceResponse",
    ("get", "/api/jobs/{job_id}/{invalid_path}"): "ArtifactResponse",
}


def _response_component(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        ref: str = schema["$ref"]
        return ref.rsplit("/", 1)[-1]
    title: str = schema.get("title", "")
    return title


def test_workspace_job_route_manifest(tmp_path: Path):
    schema = build_openapi_schema(tmp_path)
    actual = {}
    expected_paths = {path for _, path in EXPECTED_OPERATIONS}
    for path, path_item in schema["paths"].items():
        if path not in expected_paths:
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
            actual[(method, path)] = _response_component(response_schema)

    assert actual == EXPECTED_OPERATIONS


def test_openapi_export_rejects_duplicate_api_routes(tmp_path: Path, monkeypatch):
    class DuplicateResponse(BaseModel):
        value: int

    app = FastAPI()
    app.state.settings = SimpleNamespace(config={})

    @app.get("/api/duplicate", response_model=DuplicateResponse)
    def first_duplicate() -> DuplicateResponse:
        return DuplicateResponse(value=1)

    @app.get("/api/duplicate", response_model=DuplicateResponse)
    def second_duplicate() -> DuplicateResponse:
        return DuplicateResponse(value=2)

    monkeypatch.setattr(export_openapi, "create_app", lambda **_kwargs: app)

    with pytest.raises(ValueError, match="duplicate API route GET /api/duplicate"):
        build_openapi_schema(tmp_path)


@pytest.mark.parametrize(
    ("method", "path", "expected_status", "expected_detail"),
    [
        ("get", "/api/workspaces/missing", 404, "Workspace not found"),
        ("get", "/api/jobs/missing", 404, "Job not found"),
        ("delete", "/api/jobs/missing", 404, "Job not found"),
        ("get", "/api/jobs/missing/artifacts/result.json", 404, "Job not found"),
        ("get", "/api/jobs/missing/unknown", 404, "Job not found"),
    ],
)
def test_workspace_job_error_contract(client, method, path, expected_status, expected_detail):
    response = client.request(method, path)
    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}


def test_catch_all_router_does_not_shadow_job_log_endpoint(client):
    """Regression: the `/jobs/{job_id}/{invalid_path:path}` catch-all must be
    registered after `/jobs/{job_id}/runs/{run_id}/log`, otherwise valid log
    requests are misrouted and return 'Invalid job path' instead of 'Run not found'.
    """
    workspace_id, job_id = _create_test_job(client)
    response = client.get(f"/api/jobs/{job_id}/runs/999/log")
    assert response.status_code == 404
    assert response.json() == {"detail": "Run not found"}


def _create_test_job(client):
    ws_response = client.post(
        "/api/workspaces",
        json={"name": "test_ws", "default_workflow_key": "question_comprehension_info"},
    )
    assert ws_response.status_code == 200
    workspace_id = ws_response.json()["workspace"]["id"]
    response = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "question_ids": ["Q001"],
            "knowledge_codes": [],
        },
    )
    assert response.status_code == 200
    return workspace_id, response.json()["jobs"][0]["id"]


def _assert_job_summary(summary: JobSummaryResponse) -> None:
    assert isinstance(summary.completed_nodes, int)
    assert isinstance(summary.total_nodes, int)
    assert summary.active_node_key is None or isinstance(summary.active_node_key, str)
    assert isinstance(summary.error_summary, str)
    assert isinstance(summary.packed, int)
    assert isinstance(summary.execution_control, ExecutionControlSummaryResponse)
    assert isinstance(summary.node_summaries, list)
    for node_summary in summary.node_summaries:
        assert isinstance(node_summary, JobNodeSummaryResponse)
        assert isinstance(node_summary.node_key, str)
        assert isinstance(node_summary.label, str)
        assert isinstance(node_summary.status, str)
        assert isinstance(node_summary.error_message, str)


def test_get_jobs_returns_typed_job_summaries(client):
    workspace_id, job_id = _create_test_job(client)
    response = client.get(f"/api/workspaces/{workspace_id}/jobs")
    assert response.status_code == 200
    body = JobsResponse.model_validate(response.json())
    summary = next(job for job in body.jobs if job.id == job_id)
    _assert_job_summary(summary)


def test_get_workspace_jobs_returns_typed_job_summaries(client):
    workspace_id, job_id = _create_test_job(client)
    response = client.get(f"/api/workspaces/{workspace_id}/jobs")
    assert response.status_code == 200
    body = JobsResponse.model_validate(response.json())
    summary = next(job for job in body.jobs if job.id == job_id)
    _assert_job_summary(summary)


def test_get_job_detail_returns_typed_job_summary(client):
    workspace_id, job_id = _create_test_job(client)
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    body = JobDetailResponse.model_validate(response.json())
    assert body.job.id == job_id
    _assert_job_summary(body.job)
    assert isinstance(body.nodes, list)
    assert isinstance(body.runs, list)
    assert isinstance(body.artifacts, list)


def test_get_jobs_returns_absolute_storage_dir(client):
    workspace_id, job_id = _create_test_job(client)
    response = client.get(f"/api/workspaces/{workspace_id}/jobs")
    assert response.status_code == 200
    body = response.json()
    summary = next(job for job in body["jobs"] if job["id"] == job_id)
    assert Path(summary["storage_dir"]).is_absolute()
    assert summary["storage_dir"].endswith(f"{workspace_id}/{job_shard(job_id)}/{job_id}")


def test_get_job_detail_returns_absolute_storage_dir(client):
    workspace_id, job_id = _create_test_job(client)
    response = client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert Path(body["job"]["storage_dir"]).is_absolute()
    assert body["job"]["storage_dir"].endswith(f"{workspace_id}/{job_shard(job_id)}/{job_id}")
