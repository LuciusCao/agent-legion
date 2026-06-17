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


def test_job_routes_are_hidden_when_workflows_disabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = False
    with TestClient(app) as c:
        response = c.get("/api/jobs")
        workspaces = c.get("/api/workspaces")

    assert response.status_code == 404
    assert workspaces.status_code == 404
