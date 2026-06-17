def test_get_pipeline_definition_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        response = c.get("/api/pipelines/question_content")

    assert response.status_code == 200
    body = response.json()
    assert body["pipeline"]["key"] == "question_content"
    assert body["pipeline"]["label"] == "题目内容生成"
    assert body["pipeline"]["intake"]["modes"] == [
        {
            "key": "direct_ids",
            "label": "直接输入 ID",
            "input_field": "question_ids",
            "resource": "",
        },
        {
            "key": "by_knowledge",
            "label": "按知识点查询",
            "input_field": "knowledge_codes",
            "resource": "by_knowledge",
        },
    ]
    node_keys = [node["key"] for node in body["pipeline"]["nodes"]]
    assert node_keys[0] == "fetch_question_context"
    assert "assemble_package" in node_keys
    assert all("label" in node for node in body["pipeline"]["nodes"])
    fetch_node = next(
        node for node in body["pipeline"]["nodes"] if node["key"] == "fetch_question_context"
    )
    assert fetch_node["label"] == "获取题目上下文"
    graph_node = next(
        node for node in body["pipeline"]["nodes"] if node["key"] == "content_graph_generation"
    )
    assert graph_node["after"] == ["solution_decomposition"]


def test_list_pipelines_includes_registered_pipelines(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        response = c.get("/api/pipelines")

    assert response.status_code == 200
    body = response.json()
    assert any(p["key"] for p in body["pipelines"])


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
    with TestClient(app) as c:
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
        "base_url": "http://cms.internal.example.com/v2",
        "token": "secret123",
    }
    with TestClient(app) as c:
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
    with TestClient(app) as c:
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
    with TestClient(app) as c:
        response = c.get("/api/global-services")

    assert response.status_code == 200
    body = response.json()
    assert body["cms"]["tokenConfigured"] is True


def test_update_workspace_rejects_invalid_pipeline_key(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        create_resp = c.post("/api/workspaces", json={"name": "Test"})
        assert create_resp.status_code == 200, create_resp.text
        ws_id = create_resp.json()["workspace"]["id"]
        resp = c.patch(
            f"/api/workspaces/{ws_id}",
            json={"default_pipeline_key": "nonexistent"},
        )

    assert resp.status_code == 404
    assert "Unknown pipeline" in resp.json()["detail"]
