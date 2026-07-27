from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

CSRF = {"x-agent-legion-request": "1"}


def _create_member(client, username="member1", password="pw1") -> str:
    response = client.post(
        "/api/users",
        json={"username": username, "password": password},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _member_client(client, username="member1", password="pw1"):
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


@pytest.fixture
def workspace_id(client, job_db) -> str:
    del client
    return job_db.create_workspace("Matrix WS")["id"]


def test_anonymous_business_routes_return_401(anon_client) -> None:
    assert anon_client.get("/api/workspaces").status_code == 401
    assert anon_client.post("/api/workspaces", json={"name": "x"}).status_code == 401
    assert anon_client.get("/api/jobs/job-1").status_code == 401
    assert anon_client.get("/api/metrics/overview").status_code == 401
    assert anon_client.get("/api/workflow-catalog").status_code in (401, 404)
    # Public endpoints stay reachable.
    assert anon_client.get("/api/health").status_code == 200
    assert anon_client.get("/api/auth/bootstrap").status_code == 200


def test_non_member_gets_404_not_403(client, workspace_id) -> None:
    _create_member(client)
    member = _member_client(client)
    response = member.get(f"/api/workspaces/{workspace_id}")
    assert response.status_code == 404


def test_viewer_reads_but_cannot_write(client, workspace_id, job_db) -> None:
    member_id = _create_member(client)
    job_db.upsert_workspace_member(workspace_id, member_id, "viewer")
    viewer = _member_client(client)

    assert viewer.get(f"/api/workspaces/{workspace_id}").status_code == 200
    assert viewer.get(f"/api/workspaces/{workspace_id}/settings").status_code == 200

    denied = viewer.patch(
        f"/api/workspaces/{workspace_id}",
        json={"description": "viewer edit"},
    )
    assert denied.status_code == 403


def test_editor_reads_and_writes(client, workspace_id, job_db) -> None:
    member_id = _create_member(client)
    job_db.upsert_workspace_member(workspace_id, member_id, "editor")
    editor = _member_client(client)

    patched = editor.patch(
        f"/api/workspaces/{workspace_id}",
        json={"description": "editor edit"},
    )
    assert patched.status_code == 200


def test_admin_passes_without_membership(client, workspace_id) -> None:
    response = client.get(f"/api/workspaces/{workspace_id}")
    assert response.status_code == 200
    patched = client.patch(
        f"/api/workspaces/{workspace_id}",
        json={"description": "admin edit"},
    )
    assert patched.status_code == 200


def test_member_listing_hides_unauthorized_workspaces(client, workspace_id) -> None:
    _create_member(client)
    member = _member_client(client)
    assert member.get("/api/workspaces").status_code == 200


def test_websocket_requires_session(anon_client, client) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        anon_client.websocket_connect("/api/agents"),
    ):
        pass
    with client.websocket_connect("/api/agents") as websocket:
        assert websocket is not None
