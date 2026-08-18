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


def _member_client(client, job_db, username="worker-member"):
    response = client.post("/api/users", json={"username": username, "password": "pw1"})
    assert response.status_code == 201, response.text
    member_id = response.json()["id"]
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": "pw1"})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member, member_id


def test_worker_control_requires_workspace_membership(client, job_db):
    """pause/resume take the workspace scope as a query parameter; the
    membership guard must still apply (require_workspace_access honours the
    workspace_id query parameter, not just the path one)."""
    workspace_id = str(
        job_db.create_workspace(default_workflow_key="demo_workflow", name="worker-ws")["id"]
    )
    member, _ = _member_client(client, job_db)

    assert (
        member.get("/api/worker/status", params={"workspace_id": workspace_id}).status_code == 404
    )
    assert (
        member.post("/api/worker/pause", params={"workspace_id": workspace_id}).status_code == 404
    )
    assert (
        member.post("/api/worker/resume", params={"workspace_id": workspace_id}).status_code == 404
    )
    assert client.app.state.workspace_worker_control.is_paused(workspace_id) is True


def test_worker_control_viewer_reads_but_cannot_pause(client, job_db):
    workspace_id = str(
        job_db.create_workspace(default_workflow_key="demo_workflow", name="worker-ws")["id"]
    )
    member, member_id = _member_client(client, job_db)
    job_db.upsert_workspace_member(workspace_id, member_id, "viewer")

    status = member.get("/api/worker/status", params={"workspace_id": workspace_id})
    assert status.status_code == 200
    assert (
        member.post("/api/worker/pause", params={"workspace_id": workspace_id}).status_code == 403
    )
    assert (
        member.post("/api/worker/resume", params={"workspace_id": workspace_id}).status_code == 403
    )


def test_worker_control_editor_can_pause_and_resume(client, job_db):
    workspace_id = str(
        job_db.create_workspace(default_workflow_key="demo_workflow", name="worker-ws")["id"]
    )
    member, member_id = _member_client(client, job_db)
    job_db.upsert_workspace_member(workspace_id, member_id, "editor")

    resumed = member.post("/api/worker/resume", params={"workspace_id": workspace_id})
    assert resumed.status_code == 200
    assert client.app.state.workspace_worker_control.is_paused(workspace_id) is False
    paused = member.post("/api/worker/pause", params={"workspace_id": workspace_id})
    assert paused.status_code == 200
    assert client.app.state.workspace_worker_control.is_paused(workspace_id) is True
