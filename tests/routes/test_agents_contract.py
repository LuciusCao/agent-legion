from server.app.events.agents import AgentStatus


def test_agents_get_returns_typed_public_projection(client, monkeypatch) -> None:
    # client is the worker-session shared app: monkeypatch keeps the in-memory
    # agent list from leaking into later tests.
    monkeypatch.setattr(
        client.app.state.agent_manager,
        "agents",
        [
            AgentStatus(
                id="worker-1",
                name="Worker",
                busy=True,
                task_count=2,
                max_tasks=4,
                workspace_id="ws-1",
            )
        ],
    )

    response = client.get("/api/agents")

    assert response.status_code == 200
    assert response.json() == {
        "agents": [
            {
                "id": "worker-1",
                "name": "Worker",
                "busy": True,
            }
        ]
    }


def test_agents_get_openapi_uses_named_response_contract(client) -> None:
    operation = client.app.openapi()["paths"]["/api/agents"]["get"]

    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/AgentsResponse"}
