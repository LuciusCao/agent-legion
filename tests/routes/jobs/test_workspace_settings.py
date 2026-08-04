def _create_workspace(client, name="default", default_workflow_key="question_comprehension_info"):
    return client.post(
        "/api/workspaces", json={"name": name, "default_workflow_key": default_workflow_key}
    ).json()["workspace"]["id"]


def test_create_workspace_stores_resource_config_override(client_factory):
    with client_factory(workflows_enabled=True) as c:
        response = c.post(
            "/api/workspaces",
            json={
                "name": "Math V5",
                "default_workflow_key": "question_comprehension_info",
                "resource_config": {
                    "resources": {
                        "question_detail": {
                            "enabled": True,
                            "config": {
                                "subject_id": "5",
                                "api_url": "https://cms.example/question/detail?bank_version=v5",
                            },
                        }
                    }
                },
            },
        )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    binding = workspace["resource_config"]["resources"]["question_detail"]
    assert binding["config"]["subject_id"] == "5"
    assert binding["config"]["api_url"] == "https://cms.example/question/detail?bank_version=v5"


def test_workspace_rejects_legacy_cms_config(client_factory):
    with client_factory(workflows_enabled=True) as c:
        created = c.post(
            "/api/workspaces",
            json={
                "name": "Math V5",
                "default_workflow_key": "question_comprehension_info",
                "cms_config": {"subject_id": "5"},
            },
        )
        assert created.status_code == 422

        workspace_id = _create_workspace(c, name="Math V6")
        updated = c.patch(
            f"/api/workspaces/{workspace_id}",
            json={"cms_config": {"question_detail_url": "https://cms.example/question/detail"}},
        )
        assert updated.status_code == 422


def test_update_workspace_resource_config(client_factory):
    with client_factory(workflows_enabled=True) as c:
        created = c.post(
            "/api/workspaces",
            json={"name": "Math Resources", "default_workflow_key": "question_comprehension_info"},
        ).json()
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


def test_workspace_settings_without_cms_fields(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        response = c.get(f"/api/workspaces/{ws_id}/settings")

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert "cmsUrl" not in settings
    assert "cmsToken" not in settings
    assert "resources" in settings


def test_workspace_settings_returns_resource_config(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        c.patch(
            f"/api/workspaces/{ws_id}",
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
        response = c.get(f"/api/workspaces/{ws_id}/settings")

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["resources"]["question_detail"]["enabled"] is True
    assert settings["resources"]["question_detail"]["config"]["bank_version"] == "v6"


def test_patch_settings_connection_saves_resource_config(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        response = c.patch(
            f"/api/workspaces/{ws_id}/settings/connection",
            json={"resources": {"question_detail": {"enabled": False, "config": {}}}},
        )
        fetched = c.get(f"/api/workspaces/{ws_id}/settings")

    assert response.status_code == 200
    assert fetched.json()["settings"]["resources"]["question_detail"]["enabled"] is False


def test_test_connection_uses_global_cms_url(client_factory):
    def configure(app):
        app.state.settings.config["cms"] = {
            "question_detail_url": "http://cms.example/detail",
            "token": "global_token",
        }

    with client_factory(workflows_enabled=True, configure=configure) as c:
        ws_id = _create_workspace(c)
        response = c.post(f"/api/workspaces/{ws_id}/settings/test-connection")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_test_connection_fails_when_global_url_missing(client_factory):
    def configure(app):
        app.state.settings.config["cms"] = {}

    with client_factory(workflows_enabled=True, configure=configure) as c:
        ws_id = _create_workspace(c)
        response = c.post(f"/api/workspaces/{ws_id}/settings/test-connection")

    assert response.status_code == 400
    assert "Global CMS URL" in response.json()["detail"]
