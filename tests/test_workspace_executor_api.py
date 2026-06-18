def test_list_executors_endpoint(client):
    response = client.get("/api/executors")
    assert response.status_code == 200
    data = response.json()
    assert data["executors"][0] == {
        "id": "local-default",
        "kind": "local",
        "global_capacity": 16,
        "capabilities": [
            "assemble_comprehension_info",
            "assemble_package",
            "clean_and_parse",
            "fetch_question_context",
            "fetch_questions",
        ],
    }


def test_get_workspace_executor_configuration_reports_no_warnings_after_v005(client):
    workspace_response = client.post("/api/workspaces", json={"name": "Legacy"})
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["workspace"]["id"]

    # The legacy workspace_agent_assignments table is removed by V005, so no
    # migration warnings are produced.
    response = client.get(f"/api/workspaces/{workspace_id}/executor-configuration")
    assert response.status_code == 200
    data = response.json()
    assert data["allocations"] == []
    assert data["bindings"] == []
    assert data["node_limits"] == []
    assert data["migration_warnings"] == []
