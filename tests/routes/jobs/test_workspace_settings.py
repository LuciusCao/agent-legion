def test_create_workspace_stores_cms_config_override(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        response = c.post(
            "/api/workspaces",
            json={
                "name": "Math V5",
                "cms_config": {
                    "subject_id": "5",
                    "question_detail_url": "https://cms.example/question/detail?bank_version=v5",
                },
            },
        )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["cms_config"]["subject_id"] == "5"
    assert (
        workspace["cms_config"]["question_detail_url"]
        == "https://cms.example/question/detail?bank_version=v5"
    )


def test_update_workspace_cms_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        created = c.post("/api/workspaces", json={"name": "Math V5"}).json()
        workspace_id = created["workspace"]["id"]
        response = c.patch(
            f"/api/workspaces/{workspace_id}",
            json={
                "cms_config": {
                    "question_list_url": "https://cms.example/question/list?bank_version=v5",
                    "question_detail_url": "https://cms.example/question/detail?bank_version=v5",
                    "subject_id": "5",
                    "country_id": "1",
                }
            },
        )
        fetched = c.get(f"/api/workspaces/{workspace_id}")

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["cms_config"]["subject_id"] == "5"
    assert (
        workspace["cms_config"]["question_list_url"]
        == "https://cms.example/question/list?bank_version=v5"
    )
    assert fetched.json()["workspace"]["cms_config"] == workspace["cms_config"]


def test_update_workspace_resource_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        created = c.post("/api/workspaces", json={"name": "Math Resources"}).json()
        workspace_id = created["workspace"]["id"]
        response = c.patch(
            f"/api/workspaces/{workspace_id}",
            json={
                "resource_config": {
                    "resources": {
                        "question_detail": {
                            "provider": "cms.question.detail",
                            "config": {
                                "api_url": "https://cms.example/question/detail",
                                "subject_id": "5",
                            },
                        },
                        "by_knowledge": {
                            "provider": "cms.question.list_by_knowledge",
                            "config": {
                                "api_url": "https://cms.example/question/list",
                                "page_size": 50,
                            },
                        },
                    }
                }
            },
        )

    assert response.status_code == 200
    resources = response.json()["workspace"]["resource_config"]["resources"]
    assert resources["question_detail"]["provider"] == "cms.question.detail"
    assert resources["question_detail"]["config"]["subject_id"] == "5"
    assert resources["by_knowledge"]["config"]["page_size"] == 50


def test_workspace_settings_without_cms_fields(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        response = c.get("/api/workspaces/default/settings")

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert "cmsUrl" not in settings
    assert "cmsToken" not in settings
    assert "resources" in settings


def test_workspace_settings_returns_resource_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.patch(
            "/api/workspaces/default",
            json={
                "resource_config": {
                    "resources": {
                        "question_detail": {
                            "enabled": True,
                            "config": {"bank_version": "v6"},
                        }
                    }
                }
            },
        )
        response = c.get("/api/workspaces/default/settings")

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["resources"]["question_detail"]["enabled"] is True
    assert settings["resources"]["question_detail"]["config"]["bank_version"] == "v6"


def test_patch_settings_connection_saves_resource_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        response = c.patch(
            "/api/workspaces/default/settings/connection",
            json={"resources": {"question_detail": {"enabled": False, "config": {}}}},
        )
        fetched = c.get("/api/workspaces/default/settings")

    assert response.status_code == 200
    assert fetched.json()["settings"]["resources"]["question_detail"]["enabled"] is False


def test_test_connection_uses_global_cms_url(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    app.state.settings.config["cms"] = {
        "question_detail_url": "http://cms.example/detail",
        "token": "global_token",
    }
    with TestClient(app) as c:
        response = c.post("/api/workspaces/default/settings/test-connection")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_test_connection_fails_when_global_url_missing(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    app.state.settings.config["cms"] = {}
    with TestClient(app) as c:
        response = c.post("/api/workspaces/default/settings/test-connection")

    assert response.status_code == 400
    assert "Global CMS URL" in response.json()["detail"]
