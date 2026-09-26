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
    # #703 codex round 4 (P2-2)：Range 请求实际答 206（raw_response 的对象
    # 分支带 Content-Range）——契约必须声明，生成客户端才不把分段下载当
    # 异常；206 携带 Content-Range/Content-Length 头描述。
    partial = raw_path["get"]["responses"]["206"]
    assert "application/octet-stream" in partial["content"]
    assert set(partial["headers"]) == {"Content-Range", "Content-Length"}
    # 裸路由同修（同一 raw_response 构建器，Range 同样 206）。
    bare_path = schema["paths"]["/api/jobs/{job_id}/artifacts/{artifact_name}/raw"]
    assert "application/octet-stream" in bare_path["get"]["responses"]["206"]["content"]

    schemas = schema["components"]["schemas"]
    entry = schemas["ExternalArtifactEntry"]
    # required: the identity fields; metadata fields default (local rows carry
    # no content_hash/uploaded_at).
    assert set(entry["required"]) == {"name", "storage"}
    assert {"content_hash", "uploaded_at", "size_bytes", "node_key", "media_type"} <= set(
        entry["properties"]
    )


def test_mutation_result_rerun_nodes_stay_string_array(tmp_path):
    """issue #645 review P2：JobRerunByFailureResultResponse.rerun_nodes 是
    string[]（OpenAPI 不得退化为 unknown、Pydantic 必须校验）；upgrade 的
    数量统计改用不冲突的 kept_node_count / rerun_node_count。"""
    import pytest
    from pydantic import ValidationError

    from server.app.main import create_app
    from server.app.routes.job_rerun_by_failure_contracts import (
        JobRerunByFailureResultResponse,
    )

    app = create_app(data_dir=tmp_path, start_worker=False)
    schemas = app.openapi()["components"]["schemas"]

    rerun_schema = schemas["JobRerunByFailureResultResponse"]["properties"]["rerun_nodes"]
    assert rerun_schema["type"] == "array"
    assert rerun_schema["items"]["type"] == "string"

    mutation_schema = schemas["JobMutationResultResponse"]["properties"]
    assert "rerun_nodes" not in mutation_schema
    # Optional int 字段在 OpenAPI 里是 anyOf [integer, null]（ge=0）。
    assert mutation_schema["kept_node_count"]["anyOf"][0]["type"] == "integer"
    assert mutation_schema["rerun_node_count"]["anyOf"][0]["type"] == "integer"

    # Pydantic 校验恢复：字符串列表通过，非字符串成员被拒。
    ok = JobRerunByFailureResultResponse.model_validate(
        {"job_id": "j1", "operation": "rerun", "status": "succeeded", "rerun_nodes": ["a", "b"]}
    )
    assert ok.rerun_nodes == ["a", "b"]
    with pytest.raises(ValidationError):
        JobRerunByFailureResultResponse.model_validate(
            {"job_id": "j1", "operation": "rerun", "status": "succeeded", "rerun_nodes": [1, 2]}
        )
