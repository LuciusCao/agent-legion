from tests.helpers.auth import authenticate_client


def test_get_workflow_definition_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/workflows/question_comprehension_info")

    assert response.status_code == 200
    body = response.json()
    assert body["workflow"]["key"] == "question_comprehension_info"
    assert body["workflow"]["label"] == "题目审题信息生成 DAG"
    assert body["workflow"]["intake"]["modes"] == [
        {
            "key": "batch_by_knowledge",
            "label": "按知识点批量",
            "input_field": "knowledge_codes",
            "resource": "",
        },
        {
            "key": "batch_by_ids",
            "label": "按题目ID批量",
            "input_field": "question_ids",
            "resource": "",
        },
    ]
    node_keys = [node["key"] for node in body["workflow"]["nodes"]]
    assert node_keys[0] == "fetch_questions"
    assert "assemble_comprehension_info" in node_keys
    assert all("label" in node for node in body["workflow"]["nodes"])
    fetch_node = next(
        node for node in body["workflow"]["nodes"] if node["key"] == "fetch_questions"
    )
    assert fetch_node["label"] == "获取题目"
    graph_node = next(
        node
        for node in body["workflow"]["nodes"]
        if node["key"] == "assess_comprehension_difficulty"
    )
    assert graph_node["after"] == ["review_key_info", "review_possible_errors"]


def test_list_workflows_includes_registered_workflows(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/workflows")

    assert response.status_code == 200
    body = response.json()
    assert any(p["key"] for p in body["workflows"])


def test_get_resource_providers_returns_provider_list(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.settings.config["resource_providers"] = {
        "cms.question.detail": {"path": "/question/detail"},
        "cms.question.list_by_knowledge": {"path": "/question/list"},
    }
    app.state.settings.config["cms"] = {
        "env": "prod",
        "bank_version": "v5",
        "country_id": "1",
        "subject_id": "2",
    }
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/resource-providers")

    assert response.status_code == 200
    body = response.json()
    providers = {p["key"]: p for p in body["providers"]}
    assert "question_detail" in providers
    assert providers["question_detail"]["provider"] == "cms.question.detail"
    assert providers["question_detail"]["path"] == "/question/detail"
    assert providers["question_detail"]["defaultParams"] == {
        "bank_version": "v5",
        "country_id": "1",
        "subject_id": "2",
    }
    assert providers["question_detail"]["paramKeys"] == ["bank_version", "country_id", "subject_id"]
    assert "by_knowledge" in providers
    assert providers["by_knowledge"]["provider"] == "cms.question.list_by_knowledge"
    assert providers["by_knowledge"]["paramKeys"] == [
        "bank_version",
        "country_id",
        "subject_id",
        "page_size",
    ]


def test_get_global_services_returns_cms_status(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "base_url": "http://cms.example.com/v2",
        "token": "secret123",
    }
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/global-services")

    assert response.status_code == 200
    body = response.json()
    assert body["cms"]["baseUrl"] == "http://cms.***.cn/v2"
    assert body["cms"]["tokenConfigured"] is True
    assert body["cms"]["env"] == "prod"
    assert body["cms"]["healthy"] is None


def test_get_global_services_unconfigured_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    for key in (
        "BASECMS_APP_ID",
        "BASECMS_NONCE",
        "BASECMS_SECRET",
        "BASECMS_TOKEN_URL",
        "BASECMS_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.settings.config["cms"] = {"env": "dev"}
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/global-services")

    assert response.status_code == 200
    body = response.json()
    assert body["cms"]["tokenConfigured"] is False


def test_get_global_services_token_gen_configured(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "base_url": "http://cms.example/v2",
        "token_gen": {
            "app_id": "app1",
            "nonce": "n1",
            "secret": "s1",
            "url": "http://token.example/generate",
        },
    }
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/global-services")

    assert response.status_code == 200
    body = response.json()
    assert body["cms"]["tokenConfigured"] is True


def test_update_workspace_rejects_invalid_workflow_key(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        create_resp = c.post(
            "/api/workspaces",
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        assert create_resp.status_code == 200, create_resp.text
        ws_id = create_resp.json()["workspace"]["id"]
        resp = c.patch(
            f"/api/workspaces/{ws_id}",
            json={"default_workflow_key": "nonexistent"},
        )

    assert resp.status_code == 404
    assert "Unknown workflow" in resp.json()["detail"]
