def _create_workspace(
    client, name="default", default_workflow_key="education_video_problems_generation"
):
    return client.post(
        "/api/workspaces", json={"name": name, "default_workflow_key": default_workflow_key}
    ).json()["workspace"]["id"]


def test_create_workspace_stores_resource_config_override(client_factory):
    with client_factory(workflows_enabled=True) as c:
        response = c.post(
            "/api/workspaces",
            json={
                "name": "Math V5",
                "default_workflow_key": "education_video_problems_generation",
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
                "default_workflow_key": "education_video_problems_generation",
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


def test_workspace_settings_without_cms_fields(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        response = c.get(f"/api/workspaces/{ws_id}/settings")

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert "cmsUrl" not in settings
    assert "cmsToken" not in settings
    assert "resources" not in settings
    assert "resourceSchemas" not in settings
    assert "nodeConfig" in settings
    assert "nodeConfigSchemas" in settings


def test_workspace_settings_returns_node_config(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        saved = c.patch(
            f"/api/workspaces/{ws_id}/settings/nodes",
            json={"nodeConfig": {"intake_knowledge_points": {"knowledge_dir": "examples/custom"}}},
        )
        assert saved.status_code == 200, saved.text
        response = c.get(f"/api/workspaces/{ws_id}/settings")

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["nodeConfig"]["intake_knowledge_points"]["knowledge_dir"] == "examples/custom"


def test_patch_settings_nodes_saves_node_config(client_factory):
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c)
        response = c.patch(
            f"/api/workspaces/{ws_id}/settings/nodes",
            json={"nodeConfig": {"intake_knowledge_points": {"knowledge_dir": "examples/custom"}}},
        )
        fetched = c.get(f"/api/workspaces/{ws_id}/settings")

    assert response.status_code == 200
    assert (
        fetched.json()["settings"]["nodeConfig"]["intake_knowledge_points"]["knowledge_dir"]
        == "examples/custom"
    )
