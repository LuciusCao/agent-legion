def test_worker_status_with_workspace_defaults_to_paused(client):
    status = client.get("/api/worker/status", params={"workspace_id": "ws-1"})
    assert status.status_code == 200
    assert status.json() == {"paused": True}


def test_worker_pause_resume_with_workspace(client):
    paused = client.post("/api/worker/pause", params={"workspace_id": "ws-1"})
    assert paused.status_code == 200
    assert paused.json() == {"paused": True}
    assert client.app.state.workspace_worker_control.is_paused("ws-1") is True

    status = client.get("/api/worker/status", params={"workspace_id": "ws-1"})
    assert status.json() == {"paused": True}

    resumed = client.post("/api/worker/resume", params={"workspace_id": "ws-1"})
    assert resumed.status_code == 200
    assert resumed.json() == {"paused": False}
    assert client.app.state.workspace_worker_control.is_paused("ws-1") is False

    status = client.get("/api/worker/status", params={"workspace_id": "ws-1"})
    assert status.json() == {"paused": False}


def test_worker_with_agent_legion_workspace_uses_workspace_worker(client):
    # workspace_id == "agent-legion" is treated like any other workspace.
    status = client.get("/api/worker/status", params={"workspace_id": "agent-legion"})
    assert status.json() == {"paused": True}

    client.post("/api/worker/resume", params={"workspace_id": "agent-legion"})
    assert client.app.state.workspace_worker_control.is_paused("agent-legion") is False

    status = client.get("/api/worker/status", params={"workspace_id": "agent-legion"})
    assert status.json() == {"paused": False}

    client.post("/api/worker/pause", params={"workspace_id": "agent-legion"})
    assert client.app.state.workspace_worker_control.is_paused("agent-legion") is True


def test_worker_without_workspace_id_is_required(client):
    # pause/resume additionally mount reject_studio_agent_scope (STUDIO-AGENT-001),
    # so they can no longer be exercised on a bare app without auth; through the
    # real router the missing workspace_id query still fails validation with 422.
    assert client.get("/api/worker/status").status_code == 422
    assert client.post("/api/worker/pause").status_code == 422
    assert client.post("/api/worker/resume").status_code == 422
