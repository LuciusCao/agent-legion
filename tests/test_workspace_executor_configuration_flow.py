from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.main import create_app

WORKFLOW_KEY = "question_comprehension_info"


def _sort(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("workflow_key", ""),
            row.get("node_key", ""),
            row.get("executor_id", ""),
        ),
    )


def _get_config(client: TestClient, workspace_id: str) -> dict:
    response = client.get(f"/api/workspaces/{workspace_id}/executor-configuration")
    assert response.status_code == 200
    return response.json()


def _put_config(client: TestClient, workspace_id: str, payload: dict) -> dict:
    response = client.put(f"/api/workspaces/{workspace_id}/configuration", json=payload)
    return {"status_code": response.status_code, "json": response.json()}


def _expected_local_bindings(workspace_id: str) -> list[dict]:
    return [
        {
            "workflow_key": WORKFLOW_KEY,
            "node_key": "clean_and_parse",
            "executor_id": "local-default",
        },
        {
            "workflow_key": WORKFLOW_KEY,
            "node_key": "fetch_questions",
            "executor_id": "local-default",
        },
        {
            "workflow_key": WORKFLOW_KEY,
            "node_key": "assemble_comprehension_info",
            "executor_id": "local-default",
        },
    ]


@pytest.fixture
def flow_client(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield client


def test_get_filters_retired_executor_residue(tmp_path) -> None:
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        workspace_response = client.post(
            "/api/workspaces",
            json={"name": "Residue Workspace", "default_workflow_key": WORKFLOW_KEY},
        )
        assert workspace_response.status_code == 200
        workspace_id = workspace_response.json()["workspace"]["id"]

        # Seed rows left behind by the retired `pi` Executor alongside valid
        # local-default rows, bypassing PUT validation like the legacy writers did.
        app.state.job_db.replace_workspace_executor_configuration(
            workspace_id,
            [
                {"executor_id": "pi", "concurrency_limit": 2},
                {"executor_id": "local-default", "concurrency_limit": 8},
            ],
            [
                {
                    "workflow_key": WORKFLOW_KEY,
                    "node_key": "clean_and_parse",
                    "executor_id": "pi",
                },
                {
                    "workflow_key": WORKFLOW_KEY,
                    "node_key": "fetch_questions",
                    "executor_id": "local-default",
                },
            ],
            [
                {
                    "workflow_key": WORKFLOW_KEY,
                    "node_key": "clean_and_parse",
                    "concurrency_limit": 1,
                },
                {
                    "workflow_key": WORKFLOW_KEY,
                    "node_key": "fetch_questions",
                    "concurrency_limit": 2,
                },
            ],
        )

        config = _get_config(client, workspace_id)

    assert config["allocations"] == [
        {"executor_id": "local-default", "workspace_id": workspace_id, "concurrency_limit": 8},
    ]
    assert config["bindings"] == [
        {
            "workflow_key": WORKFLOW_KEY,
            "node_key": "fetch_questions",
            "executor_id": "local-default",
        },
    ]
    # The clean_and_parse limit is dropped with its filtered binding.
    assert config["node_limits"] == [
        {"workflow_key": WORKFLOW_KEY, "node_key": "fetch_questions", "concurrency_limit": 2},
    ]


def test_workspace_executor_configuration_lifecycle(flow_client: TestClient) -> None:
    client = flow_client

    # 1. Read global Executor catalog.
    catalog_response = client.get("/api/executors")
    assert catalog_response.status_code == 200
    catalog = catalog_response.json()["executors"]
    executor_ids = {executor["id"] for executor in catalog}
    assert "local-default" in executor_ids
    local_executor = next(executor for executor in catalog if executor["id"] == "local-default")
    assert local_executor["kind"] == "local"
    assert "fetch_questions" in local_executor["capabilities"]

    # Create a workspace to configure.
    workspace_response = client.post(
        "/api/workspaces",
        json={"name": "Flow Workspace", "default_workflow_key": WORKFLOW_KEY},
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["workspace"]["id"]

    # 2. Read a bootstrapped Workspace configuration.
    config = _get_config(client, workspace_id)
    assert config["allocations"] == []
    assert config["bindings"] == []
    assert config["node_limits"] == []
    assert config["migration_warnings"] == []

    # Local Nodes retain their existing Workspace Executor configuration.
    # Agent Nodes are deliberately absent: their route and capacity come from
    # the published Workflow revision and Agent Catalog.
    save_payload = {
        "settings": {"workflowKey": WORKFLOW_KEY},
        "executor_allocations": [
            {"executor_id": "local-default", "concurrency_limit": 8},
        ],
        "node_bindings": _expected_local_bindings(workspace_id),
        "node_limits": [
            {"workflow_key": WORKFLOW_KEY, "node_key": "fetch_questions", "concurrency_limit": 2},
            {"workflow_key": WORKFLOW_KEY, "node_key": "clean_and_parse", "concurrency_limit": 1},
            {
                "workflow_key": WORKFLOW_KEY,
                "node_key": "assemble_comprehension_info",
                "concurrency_limit": 1,
            },
        ],
    }
    result = _put_config(client, workspace_id, save_payload)
    assert result["status_code"] == 200

    # Reload and compare the full aggregate.
    config = _get_config(client, workspace_id)
    assert _sort(config["allocations"]) == _sort(
        [
            {"executor_id": "local-default", "workspace_id": workspace_id, "concurrency_limit": 8},
        ]
    )
    assert _sort(config["bindings"]) == _sort(save_payload["node_bindings"])
    assert _sort(config["node_limits"]) == _sort(save_payload["node_limits"])
    assert config["migration_warnings"] == []

    # Reject an Agent Node binding without changing any persisted rows.
    bad_payload = {
        "name": "Must Not Change",
        "settings": {"workflowKey": WORKFLOW_KEY},
        "executor_allocations": [
            {"executor_id": "local-default", "concurrency_limit": 8},
        ],
        "node_bindings": [
            {
                "workflow_key": WORKFLOW_KEY,
                "node_key": "generate_key_info",
                "executor_id": "local-default",
            },
        ],
        "node_limits": [],
    }
    before_config = _get_config(client, workspace_id)
    before_workspace = client.get(f"/api/workspaces/{workspace_id}").json()["workspace"]

    result = _put_config(client, workspace_id, bad_payload)
    assert result["status_code"] == 400
    assert "routed by Agent ID" in result["json"]["detail"]

    after_config = _get_config(client, workspace_id)
    after_workspace = client.get(f"/api/workspaces/{workspace_id}").json()["workspace"]
    assert after_workspace["name"] == before_workspace["name"]
    assert _sort(after_config["allocations"]) == _sort(before_config["allocations"])
    assert _sort(after_config["bindings"]) == _sort(before_config["bindings"])
    assert _sort(after_config["node_limits"]) == _sort(before_config["node_limits"])

    # No legacy Agent allocation migration or compatibility layer remains.
    config = _get_config(client, workspace_id)
    assert config["migration_warnings"] == []

    saved = _put_config(client, workspace_id, save_payload)
    assert saved["status_code"] == 200
    assert saved["json"]["executor_configuration"]["migration_warnings"] == []
