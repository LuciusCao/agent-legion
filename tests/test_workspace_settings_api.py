from pathlib import Path

from fastapi.testclient import TestClient

from server.app.main import create_app


def test_workspace_settings_round_trip(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        connection = c.patch(
            "/api/workspaces/default/settings/connection",
            json={
                "resources": {"question_detail": {"enabled": True, "config": {}}},
                "cmsUrl": "https://cms.example",
                "cmsToken": "secret",
            },
        )
        intake = c.patch(
            "/api/workspaces/default/settings/intake",
            json={
                "entityType": "video",
                "intakeModes": ["direct_ids"],
                "labelOverrides": {"direct_ids": "输入 ID"},
            },
        )
        pipeline = c.patch(
            "/api/workspaces/default/settings/pipeline",
            json={"pipelineKey": "question_content"},
        )
        fetched = c.get("/api/workspaces/default/settings")
        test_connection = c.post("/api/workspaces/default/settings/test-connection")

    assert connection.status_code == 200
    assert intake.status_code == 200
    assert pipeline.status_code == 200
    assert test_connection.status_code == 200
    settings = fetched.json()["settings"]
    assert "cmsUrl" not in settings
    assert "cmsToken" not in settings
    assert settings["resources"]["question_detail"]["enabled"] is True
    assert settings["entityType"] == "video"
    assert settings["intakeModes"] == ["direct_ids"]
    assert settings["labelOverrides"] == {"direct_ids": "输入 ID"}
    assert settings["pipelineKey"] == "question_content"
    workspace = app.state.job_db.get_workspace("default")
    assert "pipeline_config" not in workspace


def test_workspace_settings_pipeline_rejects_legacy_concurrency_fields(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        response = c.patch(
            "/api/workspaces/default/settings/pipeline",
            json={
                "pipelineKey": "question_content",
                "localConcurrency": 5,
                "agentConcurrency": 3,
                "nodeLocalConcurrency": {"fetch_question_context": 2},
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    legacy_fields = {"localConcurrency", "agentConcurrency", "nodeLocalConcurrency"}
    extra_fields = {
        e["loc"][-1] for e in detail if e.get("type") == "extra_forbidden" and e.get("loc")
    }
    assert extra_fields == legacy_fields
    workspace = app.state.job_db.get_workspace("default")
    assert "pipeline_config" not in workspace


def test_pipeline_openapi_contract_is_capability_only(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    schemas = app.openapi()["components"]["schemas"]

    node = schemas["PipelineNodeResponse"]["properties"]
    detail = schemas["PipelineDefinitionResponse"]["properties"]
    summary = schemas["PipelineSummaryResponse"]["properties"]

    assert "capability" in node
    assert "runner" not in node
    assert "agent" not in node
    assert "concurrency" not in detail
    assert "concurrency" not in summary
