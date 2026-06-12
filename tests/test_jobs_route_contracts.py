from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from scripts import export_openapi
from scripts.export_openapi import build_openapi_schema

EXPECTED_OPERATIONS = {
    ("get", "/api/resource-providers"): "ResourceProvidersResponse",
    ("get", "/api/global-services"): "GlobalServicesResponse",
    ("get", "/api/pipelines"): "PipelinesListResponse",
    ("get", "/api/pipelines/{pipeline_key}"): "PipelineResponse",
    ("get", "/api/workspaces"): "WorkspacesResponse",
    ("post", "/api/workspaces"): "WorkspaceResponse",
    ("get", "/api/workspaces/{workspace_id}"): "WorkspaceResponse",
    ("patch", "/api/workspaces/{workspace_id}"): "WorkspaceResponse",
    ("delete", "/api/workspaces/{workspace_id}"): "DeleteWorkspaceResponse",
    ("get", "/api/workspaces/{workspace_id}/stats"): "WorkspaceStatsResponse",
    ("get", "/api/workspaces/{workspace_id}/agents"): "WorkspaceAgentListResponse",
    ("post", "/api/workspaces/{workspace_id}/agents"): "WorkspaceAgentAssignmentResponse",
    ("get", "/api/workspaces/{workspace_id}/settings"): "WorkspaceSettingsResponse",
    ("put", "/api/workspaces/{workspace_id}/configuration"): "WorkspaceConfigurationResponse",
    ("patch", "/api/workspaces/{workspace_id}/settings/{section}"): "WorkspaceSettingsResponse",
    (
        "post",
        "/api/workspaces/{workspace_id}/settings/test-connection",
    ): "WorkspaceSettingsTestResponse",
    ("post", "/api/workspaces/{workspace_id}/job-batches"): "JobBatchResponse",
    ("post", "/api/job-batches"): "JobBatchResponse",
    ("get", "/api/workspaces/{workspace_id}/jobs"): "JobsResponse",
    ("post", "/api/workspaces/{workspace_id}/jobs/batch-rerun"): "BatchJobResponse",
    ("delete", "/api/workspaces/{workspace_id}/jobs/batch"): "BatchJobResponse",
    ("get", "/api/workspaces/{workspace_id}/runs"): "WorkspaceRunsResponse",
    ("get", "/api/workspaces/{workspace_id}/dag"): "WorkspaceDagResponse",
    ("get", "/api/jobs"): "JobsResponse",
    ("get", "/api/jobs/{job_id}"): "JobDetailResponse",
    ("delete", "/api/jobs/{job_id}"): "DeleteJobResponse",
    ("post", "/api/jobs/{job_id}/nodes/{node_key}/rerun"): "RerunNodeResponse",
    ("get", "/api/jobs/{job_id}/artifacts/{artifact_name}"): "ArtifactResponse",
    ("get", "/api/jobs/{job_id}/{invalid_path}"): "ArtifactResponse",
}


def _response_component(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return schema.get("title", "")


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
