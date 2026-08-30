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
    # P-0.5：execution_configuration 只剩 node_limits（allocations/bindings 已退役）。
    assert body["execution_configuration"]["node_limits"] == [
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


def test_workspace_execution_configuration_lifecycle(tmp_path):
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

        loaded = c.get(f"/api/workspaces/{ws_id}/execution-configuration")
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

        loaded = c.get(f"/api/workspaces/{ws_id}/execution-configuration")
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


def _settings_put(client, ws_id: str, settings: dict) -> object:
    """PUT /configuration with only the settings blob (no other sections)."""
    return client.put(
        f"/api/workspaces/{ws_id}/configuration",
        json={"settings": settings, "node_limits": []},
    )


def test_put_configuration_without_workflow_key_succeeds(client_factory):
    """#211 Phase 2 第二批：settings blob 无 workflowKey 不再 422。

    契约侧 workflowKey 降 optional（缺省=沿用已存，同 previewHidden 的
    兼容模式）；旧版前端 PUT 白名单缺 key 时 422 的行为就此退役。
    """
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c, "no-key")
        response = _settings_put(c, ws_id, {"entityType": "video"})

    assert response.status_code == 200, response.text
    assert response.json()["settings"]["entityType"] == "video"


def test_put_configuration_with_matching_workflow_key_round_trips(client_factory):
    """旧快照带 key（值匹配）仍是 no-op 往返：兼容窗口内显式传值不报错。"""
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c, "with-key")
        response = _settings_put(c, ws_id, {"workflowKey": ws_id, "entityType": "video"})

    assert response.status_code == 200, response.text
    # 响应侧仍下发该字段（Phase 3/4 才下线）。
    assert response.json()["settings"]["workflowKey"] == ws_id


def test_put_configuration_with_mismatched_workflow_key_is_rejected(client_factory):
    """key 与 workspace id 不匹配仍走不可变守卫（400）。"""
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c, "immutable")
        response = _settings_put(
            c, ws_id, {"workflowKey": "some_other_workflow", "entityType": "video"}
        )

    assert response.status_code == 400


def test_put_configuration_old_snapshot_round_trip_keeps_stored_fields(client_factory):
    """旧客户端快照往返（带 key、带 previewHidden）完整保留服务端状态。"""
    with client_factory(workflows_enabled=True) as c:
        ws_id = _create_workspace(c, "old-snapshot")
        # 先用 PATCH section 建立已存的 previewHidden。
        patched = c.patch(
            f"/api/workspaces/{ws_id}/settings/preview",
            json={"previewHidden": ["questions.json"]},
        )
        assert patched.status_code == 200

        # 模拟旧客户端：GET 快照（含 workflowKey + previewHidden）原样 PUT 回。
        snapshot = c.get(f"/api/workspaces/{ws_id}/settings").json()["settings"]
        legacy_put = _settings_put(
            c,
            ws_id,
            {
                "entityType": snapshot["entityType"],
                "workflowKey": snapshot["workflowKey"],
                "previewHidden": snapshot["previewHidden"],
            },
        )
        fetched = c.get(f"/api/workspaces/{ws_id}/settings")

    assert legacy_put.status_code == 200, legacy_put.text
    assert fetched.json()["settings"]["previewHidden"] == ["questions.json"]
    assert fetched.json()["settings"]["entityType"] == snapshot["entityType"]
