from pathlib import Path

from fastapi.testclient import TestClient

from server.app.main import create_app
from tests.helpers.auth import authenticate_client


def test_workspace_settings_round_trip(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.settings.config["cms"] = {
        "question_detail_url": "http://cms.example.com/question/detail",
        "token": "global_token",
    }
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"name": "test_ws", "default_workflow_key": "question_comprehension_info"},
        )
        assert ws.status_code == 200
        workspace_id = ws.json()["workspace"]["id"]
        connection = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={
                "nodeConfig": {"fetch_questions": {"bank_version": "v6"}},
            },
        )
        intake = c.patch(
            f"/api/workspaces/{workspace_id}/settings/intake",
            json={
                "entityType": "video",
                "intakeModes": ["batch_by_ids"],
                "labelOverrides": {"batch_by_ids": "输入 ID"},
            },
        )
        workflow = c.patch(
            f"/api/workspaces/{workspace_id}/settings/workflow",
            json={"workflowKey": "question_comprehension_info"},
        )
        fetched = c.get(f"/api/workspaces/{workspace_id}/settings")
        test_connection = c.post(f"/api/workspaces/{workspace_id}/settings/test-connection")

    assert connection.status_code == 200
    assert intake.status_code == 200
    assert workflow.status_code == 200
    assert test_connection.status_code == 200
    settings = fetched.json()["settings"]
    assert "cmsUrl" not in settings
    assert "cmsToken" not in settings
    assert "resources" not in settings
    assert settings["nodeConfig"]["fetch_questions"]["bank_version"] == "v6"
    assert settings["entityType"] == "video"
    assert settings["intakeModes"] == ["batch_by_ids"]
    assert settings["labelOverrides"] == {"batch_by_ids": "输入 ID"}
    assert settings["workflowKey"] == "question_comprehension_info"
    workspace = app.state.job_db.get_workspace(workspace_id)
    assert "pipeline_config" not in workspace


def test_workspace_settings_workflow_rejects_legacy_concurrency_fields(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"name": "test_ws", "default_workflow_key": "question_comprehension_info"},
        )
        assert ws.status_code == 200
        workspace_id = ws.json()["workspace"]["id"]
        response = c.patch(
            f"/api/workspaces/{workspace_id}/settings/workflow",
            json={
                "workflowKey": "question_comprehension_info",
                "localConcurrency": 5,
                "agentConcurrency": 3,
                "nodeLocalConcurrency": {"fetch_questions": 2},
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    legacy_fields = {"localConcurrency", "agentConcurrency", "nodeLocalConcurrency"}
    extra_fields = {
        e["loc"][-1] for e in detail if e.get("type") == "extra_forbidden" and e.get("loc")
    }
    assert extra_fields == legacy_fields
    workspace = app.state.job_db.get_workspace(workspace_id)
    assert "pipeline_config" not in workspace


def test_workflow_openapi_contract_is_capability_only(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    schemas = app.openapi()["components"]["schemas"]

    node = schemas["WorkflowNodeResponse"]["properties"]
    detail = schemas["WorkflowDefinitionResponse"]["properties"]
    summary = schemas["WorkflowSummaryResponse"]["properties"]

    assert "capability" in node
    assert "runner" not in node
    assert "agent" not in node
    assert "concurrency" not in detail
    assert "concurrency" not in summary


def test_lists_workspace_workflow_revisions(client):
    response = client.post(
        "/api/workspaces",
        json={"name": "Workflow Studio", "default_workflow_key": "question_comprehension_info"},
    )
    workspace_id = response.json()["workspace"]["id"]

    revisions = client.get(f"/api/workspaces/{workspace_id}/workflow-revisions")

    assert revisions.status_code == 200
    payload = revisions.json()
    assert "revisions" in payload


def _inject_key_info_config_schema(app) -> None:
    schema = {
        "type": "object",
        "properties": {
            "max_items": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100}
        },
    }
    agents = dict(app.state.settings.agent_definitions)
    original = agents["question-key-info-v1"]
    agents["question-key-info-v1"] = original.model_copy(update={"config_schema": schema})
    app.state.settings.agent_definitions = agents


def test_workspace_settings_nodes_round_trip(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    _inject_key_info_config_schema(app)
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"name": "nodes_ws", "default_workflow_key": "question_comprehension_info"},
        )
        assert ws.status_code == 200
        workspace_id = ws.json()["workspace"]["id"]

        fetched = c.get(f"/api/workspaces/{workspace_id}/settings")
        assert fetched.status_code == 200
        settings = fetched.json()["settings"]
        assert settings["nodeConfig"] == {}
        # generate_key_info comes from the Agent catalog; fetch_questions is an
        # executor capability whose schema is declared in workflow.yaml (D15).
        assert set(settings["nodeConfigSchemas"]) == {"generate_key_info", "fetch_questions"}

        saved = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"generate_key_info": {"max_items": 5}}},
        )
        assert saved.status_code == 200
        assert saved.json()["settings"]["nodeConfig"] == {"generate_key_info": {"max_items": 5}}

        saved_executor = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"fetch_questions": {"bank_version": "v6"}}},
        )
        assert saved_executor.status_code == 200
        assert saved_executor.json()["settings"]["nodeConfig"] == {
            "generate_key_info": {"max_items": 5},
            # The secret token surfaces as a write-only marker (VAULT-SECRET-001).
            "fetch_questions": {"bank_version": "v6", "token": {"secret_set": False}},
        }

        cleared = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"generate_key_info": {}, "fetch_questions": {}}},
        )
        assert cleared.status_code == 200
        assert cleared.json()["settings"]["nodeConfig"] == {}

    workspace = app.state.job_db.get_workspace(workspace_id)
    assert workspace["node_config"] == {"question_comprehension_info": {}}


def test_workspace_settings_nodes_reject_invalid_overrides(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    _inject_key_info_config_schema(app)
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"name": "nodes_ws_bad", "default_workflow_key": "question_comprehension_info"},
        )
        workspace_id = ws.json()["workspace"]["id"]

        unknown_key = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"generate_key_info": {"nope": 1}}},
        )
        out_of_bounds = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"generate_key_info": {"max_items": 0}}},
        )
        executor_unknown_key = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"fetch_questions": {"max_items": 5}}},
        )
        unknown_node = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"not_a_node": {"max_items": 5}}},
        )

    assert unknown_key.status_code == 400
    assert out_of_bounds.status_code == 400
    # fetch_questions is an executor node with a declared schema (D15);
    # max_items is not part of it.
    assert executor_unknown_key.status_code == 400
    assert unknown_node.status_code == 400


def test_workspace_settings_node_config_is_schema_validated_and_masked(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"name": "res_ws", "default_workflow_key": "question_comprehension_info"},
        )
        workspace_id = ws.json()["workspace"]["id"]

        fetched = c.get(f"/api/workspaces/{workspace_id}/settings")
        assert fetched.status_code == 200
        node_schemas = fetched.json()["settings"]["nodeConfigSchemas"]
        assert "page_size" in node_schemas["fetch_questions"]["properties"]
        assert node_schemas["fetch_questions"]["properties"]["token"]["secret"] is True

        bad_type = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"fetch_questions": {"page_size": "x"}}},
        )
        unknown_key = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"fetch_questions": {"evil": 1}}},
        )
        ok = c.patch(
            f"/api/workspaces/{workspace_id}/settings/nodes",
            json={"nodeConfig": {"fetch_questions": {"page_size": 100}}},
        )

    assert bad_type.status_code == 400
    assert unknown_key.status_code == 400
    assert ok.status_code == 200
    # Secret schema fields surface as write-only markers in the payload.
    assert ok.json()["settings"]["nodeConfig"]["fetch_questions"] == {
        "page_size": 100,
        "token": {"secret_set": False},
    }
