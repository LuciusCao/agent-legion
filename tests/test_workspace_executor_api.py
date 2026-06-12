def test_list_executors_endpoint(client):
    response = client.get("/api/executors")
    assert response.status_code == 200
    data = response.json()
    assert data["executors"][0] == {
        "id": "local-default",
        "kind": "local",
        "global_capacity": 16,
        "capabilities": [
            "assemble_package",
            "clean_and_parse",
            "fetch_question_context",
            "fetch_questions",
            "mark_question",
        ],
    }


def test_get_workspace_executor_configuration_reports_warnings(client):
    workspace_response = client.post("/api/workspaces", json={"name": "Legacy"})
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["workspace"]["id"]

    agent_response = client.post(
        f"/api/workspaces/{workspace_id}/agents",
        json={"agent_id": "unknown-agent", "concurrency_limit": 2},
    )
    assert agent_response.status_code == 200

    response = client.get(f"/api/workspaces/{workspace_id}/executor-configuration")
    assert response.status_code == 200
    data = response.json()
    assert data["allocations"] == []
    assert data["bindings"] == []
    assert data["node_limits"] == []
    assert data["migration_warnings"] == [
        "Legacy agent assignment unknown-agent has no Executor mapping"
    ]
