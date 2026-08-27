from tests.helpers.auth import authenticate_client


def _create_workspace(
    client, name="default", default_workflow_key="education_video_problems_generation"
):
    return client.post(
        "/api/workspaces",
        json={"id": default_workflow_key, "name": name},
    ).json()["workspace"]["id"]


def test_workspace_configuration_saves_all_sections_atomically(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c, "default", "education_video_problems_generation")
        response = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "name": "Updated Workspace",
                "description": "Atomic settings",
                "settings": {
                    "entityType": "video",
                    "intakeModes": ["direct_ids"],
                    "labelOverrides": {"direct_ids": "Direct IDs"},
                    "workflowKey": "education_video_problems_generation",
                },
                "node_limits": [
                    {
                        "workflow_key": "education_video_problems_generation",
                        "node_key": "publish_content",
                        "concurrency_limit": 3,
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["name"] == "Updated Workspace"
    assert body["settings"]["entityType"] == "video"
    # P-0.5：executor_configuration 只剩 node_limits（allocations/bindings 已退役）。
    assert body["executor_configuration"]["node_limits"] == [
        {
            "workflow_key": "education_video_problems_generation",
            "node_key": "publish_content",
            "concurrency_limit": 3,
        },
    ]


def test_workspace_configuration_rejects_invalid_node_limit_without_partial_update(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c, "rollback", "education_video_problems_generation")

        # Establish a known good configuration first.
        c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "name": "Rollback Test",
                "settings": {"workflowKey": "education_video_problems_generation"},
                "node_limits": [
                    {
                        "workflow_key": "education_video_problems_generation",
                        "node_key": "publish_content",
                        "concurrency_limit": 2,
                    },
                ],
            },
        )
        original_limits = app.state.job_db.get_workspace_node_limits(ws_id)

        response = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "name": "Must Roll Back",
                "settings": {"workflowKey": "education_video_problems_generation"},
                "node_limits": [
                    {
                        "workflow_key": "education_video_problems_generation",
                        "node_key": "unknown_node",
                        "concurrency_limit": 1,
                    },
                ],
            },
        )
        persisted = c.get(f"/api/workspaces/{ws_id}").json()["workspace"]

    assert response.status_code == 400
    # The invalid PUT must not change the workspace or its node limits.
    assert persisted["name"] == "Rollback Test"
    assert app.state.job_db.get_workspace_node_limits(ws_id) == original_limits


def test_workspace_executor_configuration_lifecycle(tmp_path):
    """P-0.5: GET returns node limits only; a PUT echoing the GET round-trips."""
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c, "lifecycle", "education_video_problems_generation")
        saved = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "settings": {"workflowKey": "education_video_problems_generation"},
                "node_limits": [
                    {
                        "workflow_key": "education_video_problems_generation",
                        "node_key": "publish_content",
                        "concurrency_limit": 2,
                    },
                ],
            },
        )
        assert saved.status_code == 200

        loaded = c.get(f"/api/workspaces/{ws_id}/executor-configuration")
        assert loaded.status_code == 200
        config = loaded.json()
        assert config["node_limits"] == [
            {
                "workflow_key": "education_video_problems_generation",
                "node_key": "publish_content",
                "concurrency_limit": 2,
            }
        ]
        assert "allocations" not in config
        assert "bindings" not in config

        # The frontend saves by echoing back what GET returned.
        echoed = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "settings": {"workflowKey": "education_video_problems_generation"},
                "node_limits": config["node_limits"],
            },
        )
        assert echoed.status_code == 200

    assert app.state.job_db.get_workspace_node_limits(ws_id) == [
        {
            "workflow_key": "education_video_problems_generation",
            "node_key": "publish_content",
            "concurrency_limit": 2,
        }
    ]


def test_workspace_configuration_agent_capacity_round_trip(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c, "capacity", "education_video_problems_generation")
        saved = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "settings": {"workflowKey": "education_video_problems_generation"},
                "node_limits": [],
                "agent_capacity": 7,
            },
        )
        assert saved.status_code == 200
        assert saved.json()["agent_capacity"] == 7

        loaded = c.get(f"/api/workspaces/{ws_id}/executor-configuration")
        assert loaded.status_code == 200
        assert loaded.json()["agent_capacity"] == 7

        # Omitting agent_capacity leaves the saved value unchanged.
        omitted = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "settings": {"workflowKey": "education_video_problems_generation"},
                "node_limits": [],
            },
        )
        assert omitted.status_code == 200
        assert omitted.json()["agent_capacity"] == 7

        invalid = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "settings": {"workflowKey": "education_video_problems_generation"},
                "node_limits": [],
                "agent_capacity": 0,
            },
        )
        assert invalid.status_code == 422
