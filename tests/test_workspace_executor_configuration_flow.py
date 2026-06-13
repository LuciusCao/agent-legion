from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.main import create_app

PIPELINE_KEY = "reading_analysis"


def _sort(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("pipeline_key", ""),
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
            "pipeline_key": PIPELINE_KEY,
            "node_key": "clean_and_parse",
            "executor_id": "local-default",
        },
        {
            "pipeline_key": PIPELINE_KEY,
            "node_key": "fetch_questions",
            "executor_id": "local-default",
        },
        {"pipeline_key": PIPELINE_KEY, "node_key": "mark_question", "executor_id": "local-default"},
    ]


def _expected_pi_bindings(workspace_id: str) -> list[dict]:
    return [
        {
            "pipeline_key": PIPELINE_KEY,
            "node_key": "assess_difficulty",
            "executor_id": "pi-default",
        },
        {"pipeline_key": PIPELINE_KEY, "node_key": "review_keywords", "executor_id": "pi-default"},
    ]


@pytest.fixture
def flow_client(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield client


def test_workspace_executor_configuration_lifecycle(flow_client: TestClient) -> None:
    client = flow_client

    # 1. Read global Executor catalog.
    catalog_response = client.get("/api/executors")
    assert catalog_response.status_code == 200
    catalog = catalog_response.json()["executors"]
    executor_ids = {executor["id"] for executor in catalog}
    assert "local-default" in executor_ids
    assert "pi-default" in executor_ids
    local_executor = next(executor for executor in catalog if executor["id"] == "local-default")
    pi_executor = next(executor for executor in catalog if executor["id"] == "pi-default")
    assert local_executor["kind"] == "local"
    assert "fetch_questions" in local_executor["capabilities"]
    assert pi_executor["kind"] == "pi"
    assert "review_keywords" in pi_executor["capabilities"]

    # Create a workspace to configure.
    workspace_response = client.post(
        "/api/workspaces",
        json={"name": "Flow Workspace", "default_pipeline_key": PIPELINE_KEY},
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["workspace"]["id"]

    # 2. Read a bootstrapped Workspace configuration.
    config = _get_config(client, workspace_id)
    assert config["allocations"] == []
    assert config["bindings"] == []
    assert config["node_limits"] == []
    assert config["migration_warnings"] == []

    # 3. Allocate Pi and local Executors.
    # 4. Bind compatible Nodes.
    # 5. Leave one Node unbound (e.g. generate_distractors).
    save_payload = {
        "settings": {"pipelineKey": PIPELINE_KEY},
        "executor_allocations": [
            {"executor_id": "local-default", "concurrency_limit": 8},
            {"executor_id": "pi-default", "concurrency_limit": 10},
        ],
        "node_bindings": _expected_local_bindings(workspace_id)
        + _expected_pi_bindings(workspace_id),
        "node_limits": [
            {"pipeline_key": PIPELINE_KEY, "node_key": "fetch_questions", "concurrency_limit": 2},
            {"pipeline_key": PIPELINE_KEY, "node_key": "clean_and_parse", "concurrency_limit": 1},
            {"pipeline_key": PIPELINE_KEY, "node_key": "mark_question", "concurrency_limit": 1},
        ],
    }
    result = _put_config(client, workspace_id, save_payload)
    assert result["status_code"] == 200

    # 7. Reload and compare the full aggregate.
    config = _get_config(client, workspace_id)
    assert _sort(config["allocations"]) == _sort(
        [
            {"executor_id": "local-default", "workspace_id": workspace_id, "concurrency_limit": 8},
            {"executor_id": "pi-default", "workspace_id": workspace_id, "concurrency_limit": 10},
        ]
    )
    assert _sort(config["bindings"]) == _sort(save_payload["node_bindings"])
    assert _sort(config["node_limits"]) == _sort(save_payload["node_limits"])
    assert config["migration_warnings"] == []

    # 8. Reject an unsupported binding without changing any persisted rows.
    bad_payload = {
        "name": "Must Not Change",
        "settings": {"pipelineKey": PIPELINE_KEY},
        "executor_allocations": [
            {"executor_id": "local-default", "concurrency_limit": 8},
            {"executor_id": "pi-default", "concurrency_limit": 10},
        ],
        "node_bindings": [
            {
                "pipeline_key": PIPELINE_KEY,
                "node_key": "mark_question",
                "executor_id": "pi-default",
            },
        ],
        "node_limits": [],
    }
    before_config = _get_config(client, workspace_id)
    before_workspace = client.get(f"/api/workspaces/{workspace_id}").json()["workspace"]

    result = _put_config(client, workspace_id, bad_payload)
    assert result["status_code"] == 400
    assert "does not support capability" in result["json"]["detail"]

    after_config = _get_config(client, workspace_id)
    after_workspace = client.get(f"/api/workspaces/{workspace_id}").json()["workspace"]
    assert after_workspace["name"] == before_workspace["name"]
    assert _sort(after_config["allocations"]) == _sort(before_config["allocations"])
    assert _sort(after_config["bindings"]) == _sort(before_config["bindings"])
    assert _sort(after_config["node_limits"]) == _sort(before_config["node_limits"])

    # 9. Remove Pi allocation and its bindings in one request.
    remove_pi_payload = {
        "settings": {"pipelineKey": PIPELINE_KEY},
        "executor_allocations": [
            {"executor_id": "local-default", "concurrency_limit": 8},
        ],
        "node_bindings": _expected_local_bindings(workspace_id),
        "node_limits": [
            {"pipeline_key": PIPELINE_KEY, "node_key": "fetch_questions", "concurrency_limit": 2},
            {"pipeline_key": PIPELINE_KEY, "node_key": "clean_and_parse", "concurrency_limit": 1},
            {"pipeline_key": PIPELINE_KEY, "node_key": "mark_question", "concurrency_limit": 1},
        ],
    }
    result = _put_config(client, workspace_id, remove_pi_payload)
    assert result["status_code"] == 200

    config = _get_config(client, workspace_id)
    assert config["allocations"] == [
        {"executor_id": "local-default", "workspace_id": workspace_id, "concurrency_limit": 8},
    ]
    assert _sort(config["bindings"]) == _sort(_expected_local_bindings(workspace_id))
    assert _sort(config["node_limits"]) == _sort(remove_pi_payload["node_limits"])

    # 10. After V005 the legacy workspace_agent_assignments table is dropped;
    # no migration warnings are produced.
    config = _get_config(client, workspace_id)
    assert config["migration_warnings"] == []

    saved = _put_config(client, workspace_id, remove_pi_payload)
    assert saved["status_code"] == 200
    assert saved["json"]["executor_configuration"]["migration_warnings"] == []
