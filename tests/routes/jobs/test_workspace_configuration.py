def _create_workspace(client, name="default", default_workflow_key="question_comprehension_info"):
    return client.post(
        "/api/workspaces",
        json={"name": name, "default_workflow_key": default_workflow_key},
    ).json()["workspace"]["id"]


def test_workspace_configuration_saves_all_sections_atomically(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c, "default", "question_comprehension_info")
        response = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "name": "Updated Workspace",
                "description": "Atomic settings",
                "settings": {
                    "entityType": "video",
                    "intakeModes": ["batch_by_ids"],
                    "labelOverrides": {"batch_by_ids": "Video IDs"},
                    "workflowKey": "question_comprehension_info",
                    "resources": {"question_detail": {"enabled": True, "config": {}}},
                },
                "executor_allocations": [
                    {"executor_id": "local-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "fetch_questions",
                        "executor_id": "local-default",
                    },
                ],
                "node_limits": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "fetch_questions",
                        "concurrency_limit": 3,
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["name"] == "Updated Workspace"
    assert body["settings"]["entityType"] == "video"
    assert body["executor_configuration"]["allocations"] == [
        {"executor_id": "local-default", "workspace_id": ws_id, "concurrency_limit": 4},
    ]
    assert body["executor_configuration"]["bindings"] == [
        {
            "workflow_key": "question_comprehension_info",
            "node_key": "fetch_questions",
            "executor_id": "local-default",
        },
    ]
    assert body["executor_configuration"]["node_limits"] == [
        {
            "workflow_key": "question_comprehension_info",
            "node_key": "fetch_questions",
            "concurrency_limit": 3,
        },
    ]


def test_workspace_configuration_rejects_invalid_binding_without_partial_update(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c, "rollback", "question_comprehension_info")

        # Establish a known good configuration first.
        c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "name": "Rollback Test",
                "settings": {"workflowKey": "question_comprehension_info"},
                "executor_allocations": [
                    {"executor_id": "local-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "fetch_questions",
                        "executor_id": "local-default",
                    },
                ],
                "node_limits": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "fetch_questions",
                        "concurrency_limit": 2,
                    },
                ],
            },
        )
        original_config = app.state.job_db.get_workspace_executor_configuration(ws_id)

        response = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "name": "Must Roll Back",
                "settings": {"workflowKey": "question_comprehension_info"},
                "executor_allocations": [
                    {"executor_id": "local-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "unknown_node",
                        "executor_id": "local-default",
                    },
                ],
                "node_limits": [],
            },
        )
        persisted = c.get(f"/api/workspaces/{ws_id}").json()["workspace"]

    assert response.status_code == 400
    # The invalid PUT must not change the workspace or its executor configuration.
    assert persisted["name"] == "Rollback Test"
    config = app.state.job_db.get_workspace_executor_configuration(ws_id)
    assert config == original_config


def test_app_startup_materializes_executor_configuration_for_workspace(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.jobs import JobQueries
    from server.app.main import create_app
    from tests.helpers import ensure_legacy_workspace_tables

    db_path = tmp_path / "video_hive.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir=jobs_dir)
    ensure_legacy_workspace_tables(queries)
    workspace = queries.create_workspace(
        "Materialized", default_workflow_key="question_comprehension_info"
    )
    ws_id = workspace["id"]
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_agent_assignments(workspace_id, agent_id, concurrency_limit) values (?, ?, ?)",
            (ws_id, "pi", 3),
        )

    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as c:
        response = c.get(f"/api/workspaces/{ws_id}/executor-configuration")

    assert response.status_code == 200
    assert {row["executor_id"] for row in response.json()["allocations"]} == {
        "local-default",
        "pi",
    }
