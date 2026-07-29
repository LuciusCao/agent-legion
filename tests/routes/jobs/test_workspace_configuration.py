from tests.helpers.auth import authenticate_client
from tests.postgres_support import TEST_DATABASE_URL


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
    with authenticate_client(TestClient(app)) as c:
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
                },
                "executor_allocations": [
                    {"executor_id": "code-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "fetch_questions",
                        "executor_id": "code-default",
                    },
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "clean_and_parse",
                        "executor_id": "code-default",
                    },
                ],
                "node_limits": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "clean_and_parse",
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
        {"executor_id": "code-default", "workspace_id": ws_id, "concurrency_limit": 4},
    ]
    assert body["executor_configuration"]["bindings"] == [
        {
            "workflow_key": "question_comprehension_info",
            "node_key": "clean_and_parse",
            "executor_id": "code-default",
        },
        {
            "workflow_key": "question_comprehension_info",
            "node_key": "fetch_questions",
            "executor_id": "code-default",
        },
    ]
    assert body["executor_configuration"]["node_limits"] == [
        {
            "workflow_key": "question_comprehension_info",
            "node_key": "clean_and_parse",
            "concurrency_limit": 3,
        },
    ]


def test_workspace_configuration_rejects_invalid_binding_without_partial_update(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c, "rollback", "question_comprehension_info")

        # Establish a known good configuration first.
        c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "name": "Rollback Test",
                "settings": {"workflowKey": "question_comprehension_info"},
                "executor_allocations": [
                    {"executor_id": "code-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "clean_and_parse",
                        "executor_id": "code-default",
                    },
                ],
                "node_limits": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "clean_and_parse",
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
                    {"executor_id": "code-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "workflow_key": "question_comprehension_info",
                        "node_key": "unknown_node",
                        "executor_id": "code-default",
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


def test_app_startup_preserves_local_executor_configuration_for_workspace(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.jobs import JobQueries
    from server.app.main import create_app

    db_path = TEST_DATABASE_URL
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir=jobs_dir)
    workspace = queries.create_workspace(
        "Materialized", default_workflow_key="question_comprehension_info"
    )
    ws_id = workspace["id"]
    queries.replace_workspace_executor_configuration(
        ws_id,
        [
            {"executor_id": "code-default", "concurrency_limit": 1},
        ],
        [],
        [],
    )

    app = create_app(data_dir=tmp_path, start_worker=False)
    with authenticate_client(TestClient(app)) as c:
        response = c.get(f"/api/workspaces/{ws_id}/executor-configuration")

    assert response.status_code == 200
    assert {row["executor_id"] for row in response.json()["allocations"]} == {"code-default"}


def test_workspace_configuration_put_lazily_cleans_retired_executor_residue(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c, "residue", "question_comprehension_info")
        # Seed rows left behind by the retired `pi` Executor alongside valid
        # code-default rows, bypassing PUT validation like the legacy writers
        # did.
        app.state.job_db.replace_workspace_executor_configuration(
            ws_id,
            [
                {"executor_id": "pi", "concurrency_limit": 2},
                {"executor_id": "code-default", "concurrency_limit": 8},
            ],
            [
                {
                    "workflow_key": "question_comprehension_info",
                    "node_key": "clean_and_parse",
                    "executor_id": "pi",
                },
                {
                    "workflow_key": "question_comprehension_info",
                    "node_key": "fetch_questions",
                    "executor_id": "code-default",
                },
                {
                    "workflow_key": "question_comprehension_info",
                    "node_key": "finalize_non_uploadable",
                    "executor_id": "code-default",
                },
            ],
            [
                {
                    "workflow_key": "question_comprehension_info",
                    "node_key": "clean_and_parse",
                    "concurrency_limit": 1,
                },
                {
                    "workflow_key": "question_comprehension_info",
                    "node_key": "finalize_non_uploadable",
                    "concurrency_limit": 2,
                },
            ],
        )

        # The frontend saves by echoing back what GET returned.
        loaded = c.get(f"/api/workspaces/{ws_id}/executor-configuration")
        assert loaded.status_code == 200
        config = loaded.json()
        response = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "settings": {"workflowKey": "question_comprehension_info"},
                "executor_allocations": [
                    {
                        "executor_id": row["executor_id"],
                        "concurrency_limit": row["concurrency_limit"],
                    }
                    for row in config["allocations"]
                ],
                "node_bindings": config["bindings"],
                "node_limits": config["node_limits"],
            },
        )
        assert response.status_code == 200

    # The successful full replace physically removed the retired Executor rows.
    persisted = app.state.job_db.get_workspace_executor_configuration(ws_id)
    assert {row["executor_id"] for row in persisted["allocations"]} == {"code-default"}
    assert {row["executor_id"] for row in persisted["bindings"]} == {"code-default"}
    assert [(row["workflow_key"], row["node_key"]) for row in persisted["node_limits"]] == [
        ("question_comprehension_info", "finalize_non_uploadable")
    ]


def test_workspace_configuration_agent_capacity_round_trip(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c, "capacity", "question_comprehension_info")
        saved = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "settings": {"workflowKey": "question_comprehension_info"},
                "executor_allocations": [],
                "node_bindings": [],
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
                "settings": {"workflowKey": "question_comprehension_info"},
                "executor_allocations": [],
                "node_bindings": [],
                "node_limits": [],
            },
        )
        assert omitted.status_code == 200
        assert omitted.json()["agent_capacity"] == 7

        invalid = c.put(
            f"/api/workspaces/{ws_id}/configuration",
            json={
                "settings": {"workflowKey": "question_comprehension_info"},
                "executor_allocations": [],
                "node_bindings": [],
                "node_limits": [],
                "agent_capacity": 0,
            },
        )
        assert invalid.status_code == 422
