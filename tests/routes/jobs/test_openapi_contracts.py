from tests.helpers.auth import authenticate_client


def test_delete_job_response_model_is_exposed_in_openapi(tmp_path):
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    schema = app.openapi()

    assert (
        schema["paths"]["/api/jobs/{job_id}"]["delete"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/DeleteJobResponse"
    )

    schemas = schema["components"]["schemas"]
    delete_schema = schemas["DeleteJobResponse"]
    assert set(delete_schema["required"]) == {"deleted"}
    assert delete_schema["properties"]["deleted"]["type"] == "string"


def test_workspace_agent_routes_are_absent_from_openapi(tmp_path):
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    schema = app.openapi()

    assert "/api/workspaces/{workspace_id}/agents" not in schema["paths"]
    assert "WorkspaceAgentListResponse" not in schema["components"]["schemas"]
    assert "WorkspaceAgentAssignmentResponse" not in schema["components"]["schemas"]
    assert "WorkspaceAgentConfig" not in schema["components"]["schemas"]


def test_job_routes_are_available_without_former_gate(tmp_path):
    """#385/#389: the workflows.enabled 404 gate is retired — the core API
    surface answers regardless of deployment shape (a pure-remote host still
    serves definitions, runs and artifacts read-only)."""
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/workspaces/ws1/jobs")
        workspaces = c.get("/api/workspaces")

    # No longer 404-by-gate: the routes exist (auth/lookup errors are fine,
    # "Workflows are disabled" is gone).
    assert response.status_code != 404 or "disabled" not in response.json().get("detail", "")
    assert workspaces.status_code != 404 or "disabled" not in workspaces.json().get("detail", "")


def test_external_artifact_routes_contract(tmp_path):
    """#631: the three workspace-prefixed read endpoints are exposed with
    their response models (the raw download stays application/octet-stream,
    no JSON schema)."""
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    schema = app.openapi()

    status_path = schema["paths"]["/api/workspaces/{workspace_id}/jobs/{job_id}"]
    assert (
        status_path["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ExternalJobStatusResponse"
    )
    list_path = schema["paths"]["/api/workspaces/{workspace_id}/jobs/{job_id}/artifacts"]
    assert (
        list_path["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ExternalArtifactListResponse"
    )
    raw_path = schema["paths"][
        "/api/workspaces/{workspace_id}/jobs/{job_id}/artifacts/{artifact_name}/raw"
    ]
    assert "application/octet-stream" in raw_path["get"]["responses"]["200"]["content"]

    schemas = schema["components"]["schemas"]
    entry = schemas["ExternalArtifactEntry"]
    # required: the identity fields; metadata fields default (local rows carry
    # no content_hash/uploaded_at).
    assert set(entry["required"]) == {"name", "storage"}
    assert {"content_hash", "uploaded_at", "size_bytes", "node_key", "media_type"} <= set(
        entry["properties"]
    )
