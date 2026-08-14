from server.app.events.agents import AgentStatus


def test_agents_get_returns_typed_public_projection(client, monkeypatch) -> None:
    # client is the worker-session shared app: monkeypatch keeps the in-memory
    # agent list from leaking into later tests.
    monkeypatch.setattr(
        client.app.state.agent_manager,
        "agents",
        [
            AgentStatus(
                id="pi",
                name="Pi Agent",
                busy=True,
                task_count=2,
                max_tasks=4,
                workspace_id="ws-1",
                current_video_id="video-1",
                current_title="Video 1",
                current_content_type="knowledge",
                current_external_id="K001",
                current_phase="transcribe",
            )
        ],
    )

    response = client.get("/api/agents")

    assert response.status_code == 200
    assert response.json() == {
        "agents": [
            {
                "id": "pi",
                "name": "Pi Agent",
                "busy": True,
                "current_video_id": "video-1",
                "current_title": "Video 1",
                "current_content_type": "knowledge",
                "current_external_id": "K001",
                "current_phase": "transcribe",
            }
        ]
    }


def test_agents_get_openapi_uses_named_response_contract(client) -> None:
    operation = client.app.openapi()["paths"]["/api/agents"]["get"]

    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/AgentsResponse"}
