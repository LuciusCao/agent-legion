def _create_workspace(client) -> str:
    response = client.post(
        "/api/workspaces",
        json={"name": "catalog-ws", "default_workflow_key": "education_video_problems_generation"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["workspace"]["id"])


def test_executor_catalog_exposes_only_agents(client_factory):
    """P-0.5（schema v47）：executors 半区随概念退役，catalog 只剩 Agent。"""
    with client_factory() as client:
        workspace_id = _create_workspace(client)
        response = client.get("/api/executors", params={"workspace_id": workspace_id})

    assert response.status_code == 200
    data = response.json()
    assert "executors" not in data
    agent_ids = {agent["id"] for agent in data["agents"]}
    assert "example-review-questions-v1" in agent_ids
