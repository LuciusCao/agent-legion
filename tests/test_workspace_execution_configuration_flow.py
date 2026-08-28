from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.main import create_app
from tests.helpers.auth import authenticate_client

WORKFLOW_KEY = "education_video_problems_generation"


def _get_config(client: TestClient, workspace_id: str) -> dict:
    response = client.get(f"/api/workspaces/{workspace_id}/execution-configuration")
    assert response.status_code == 200
    return response.json()


def _put_config(client: TestClient, workspace_id: str, payload: dict) -> dict:
    response = client.put(f"/api/workspaces/{workspace_id}/configuration", json=payload)
    return {"status_code": response.status_code, "json": response.json()}


@pytest.fixture
def flow_client(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    with authenticate_client(TestClient(app)) as client:
        yield client


def test_workspace_execution_configuration_lifecycle(flow_client: TestClient) -> None:
    """P-0.5：执行配置只剩 node_limits + agent_capacity；catalog 只剩 Agent 半边。"""
    client = flow_client

    # 1. The execution catalog exposes only workspace-scoped Agents (schema
    # v46 + v47: the executors half is retired).
    catalog_response = client.get("/api/agent-catalog", params={"workspace_id": "ws-none"})
    assert catalog_response.status_code == 200
    assert "executors" not in catalog_response.json()

    # Create a workspace to configure.
    workspace_response = client.post(
        "/api/workspaces",
        json={"id": WORKFLOW_KEY, "name": "Flow Workspace"},
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["workspace"]["id"]

    # 2. Read a bootstrapped Workspace configuration.
    config = _get_config(client, workspace_id)
    assert config["node_limits"] == []
    assert config["migration_warnings"] == []

    # Code-pool nodes accept limits; Agent-routed nodes are deliberately
    # rejected (their concurrency comes from the Agent capacity, P-0.5).
    save_payload = {
        "settings": {"workflowKey": WORKFLOW_KEY},
        "node_limits": [
            {
                "workflow_key": WORKFLOW_KEY,
                "node_key": "intake_knowledge_points",
                "concurrency_limit": 1,
            },
            {
                "workflow_key": WORKFLOW_KEY,
                "node_key": "publish_content",
                "concurrency_limit": 1,
            },
        ],
    }
    result = _put_config(client, workspace_id, save_payload)
    assert result["status_code"] == 200

    config = _get_config(client, workspace_id)
    assert config["node_limits"] == save_payload["node_limits"]
    assert config["migration_warnings"] == []

    # Reject an Agent-routed node limit without changing any persisted rows.
    bad_payload = {
        "name": "Must Not Change",
        "settings": {"workflowKey": WORKFLOW_KEY},
        "node_limits": [
            {
                "workflow_key": WORKFLOW_KEY,
                "node_key": "write_script",
                "concurrency_limit": 1,
            },
        ],
    }
    before_config = _get_config(client, workspace_id)
    before_workspace = client.get(f"/api/workspaces/{workspace_id}").json()["workspace"]

    result = _put_config(client, workspace_id, bad_payload)
    assert result["status_code"] == 400
    assert "Agent-routed" in result["json"]["detail"]

    after_config = _get_config(client, workspace_id)
    after_workspace = client.get(f"/api/workspaces/{workspace_id}").json()["workspace"]
    assert after_workspace["name"] == before_workspace["name"]
    assert after_config["node_limits"] == before_config["node_limits"]

    saved = _put_config(client, workspace_id, save_payload)
    assert saved["status_code"] == 200
    assert saved["json"]["execution_configuration"]["migration_warnings"] == []
