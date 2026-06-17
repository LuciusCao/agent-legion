def test_workspace_configuration_saves_all_sections_atomically(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        response = c.put(
            "/api/workspaces/default/configuration",
            json={
                "name": "Updated Workspace",
                "description": "Atomic settings",
                "settings": {
                    "entityType": "video",
                    "intakeModes": ["direct_ids"],
                    "labelOverrides": {"direct_ids": "Video IDs"},
                    "pipelineKey": "question_content",
                    "resources": {"question_detail": {"enabled": True, "config": {}}},
                },
                "executor_allocations": [
                    {"executor_id": "local-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "pipeline_key": "question_content",
                        "node_key": "fetch_question_context",
                        "executor_id": "local-default",
                    },
                ],
                "node_limits": [
                    {
                        "pipeline_key": "question_content",
                        "node_key": "fetch_question_context",
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
        {"executor_id": "local-default", "workspace_id": "default", "concurrency_limit": 4},
    ]
    assert body["executor_configuration"]["bindings"] == [
        {
            "pipeline_key": "question_content",
            "node_key": "fetch_question_context",
            "executor_id": "local-default",
        },
    ]
    assert body["executor_configuration"]["node_limits"] == [
        {
            "pipeline_key": "question_content",
            "node_key": "fetch_question_context",
            "concurrency_limit": 3,
        },
    ]


def test_workspace_configuration_rejects_invalid_binding_without_partial_update(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        original = c.get("/api/workspaces/default").json()["workspace"]
        response = c.put(
            "/api/workspaces/default/configuration",
            json={
                "name": "Must Roll Back",
                "settings": {"pipelineKey": "question_content"},
                "executor_allocations": [
                    {"executor_id": "local-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "pipeline_key": "question_content",
                        "node_key": "unknown_node",
                        "executor_id": "local-default",
                    },
                ],
                "node_limits": [],
            },
        )
        persisted = c.get("/api/workspaces/default").json()["workspace"]

    assert response.status_code == 400
    assert persisted["name"] == original["name"]
    config = app.state.job_db.get_workspace_executor_configuration("default")
    # Startup bootstrap materialized reading_analysis defaults for the default
    # workspace; the failed PUT rolls back to that state.
    assert config["allocations"] == [
        {"workspace_id": "default", "executor_id": "local-default", "concurrency_limit": 1}
    ]
    assert config["bindings"] == [
        {
            "pipeline_key": "reading_analysis",
            "node_key": "clean_and_parse",
            "executor_id": "local-default",
        },
        {
            "pipeline_key": "reading_analysis",
            "node_key": "fetch_questions",
            "executor_id": "local-default",
        },
    ]
    assert config["node_limits"] == [
        {
            "pipeline_key": "reading_analysis",
            "node_key": "clean_and_parse",
            "concurrency_limit": 1,
        },
        {
            "pipeline_key": "reading_analysis",
            "node_key": "fetch_questions",
            "concurrency_limit": 1,
        },
    ]


def test_app_startup_materializes_executor_configuration_for_default_workspace(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.jobs import JobQueries
    from server.app.main import create_app
    from tests.helpers import ensure_legacy_workspace_tables

    db_path = tmp_path / "video_hive.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir=jobs_dir)
    ensure_legacy_workspace_tables(queries)
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_agent_assignments(workspace_id, agent_id, concurrency_limit) values (?, ?, ?)",
            ("default", "pi", 3),
        )

    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as c:
        response = c.get("/api/workspaces/default/executor-configuration")

    assert response.status_code == 200
    assert {row["executor_id"] for row in response.json()["allocations"]} == {
        "local-default",
        "pi-default",
    }
