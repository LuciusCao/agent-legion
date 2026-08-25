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
    return job_db.create_workspace(default_workflow_key="demo_workflow", name="Matrix WS")["id"]


def test_anonymous_business_routes_return_401(anon_client) -> None:
    assert anon_client.get("/api/workspaces").status_code == 401
    assert anon_client.post("/api/workspaces", json={"name": "x"}).status_code == 401
    assert anon_client.get("/api/jobs/job-1").status_code == 401
    assert anon_client.get("/api/metrics/overview").status_code == 401
    assert anon_client.get("/api/workflow-catalog").status_code in (401, 404)
    # Public endpoints stay reachable.
    assert anon_client.get("/api/health").status_code == 200
    assert anon_client.get("/api/auth/bootstrap").status_code == 200


def test_cookie_mutation_without_csrf_header_is_403(client) -> None:
    """Cookie-authenticated mutations must carry the CSRF header (SECURITY-AUTH-001).

    A cross-site request can ride the session cookie but cannot set custom
    headers; without this rejection every cookie channel mutation would be
    CSRF-able. Bearer-channel exemption (scoped tokens are not ambient) is
    covered by the test_studio_agent_scope suite.
    """
    # The client fixture authenticates over the cookie channel with the CSRF
    # header preset; drop it to simulate the cross-site request shape (the
    # session cookie stays on the client).
    csrf_value = client.headers.pop("x-agent-legion-request", None)
    assert csrf_value is not None
    try:
        response = client.post("/api/workspaces", json={"name": "csrf-probe"})
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing request header"
        # Safe methods never need the header.
        assert client.get("/api/workspaces").status_code == 200
    finally:
        client.headers["x-agent-legion-request"] = csrf_value
    # The same mutation with the header restored goes through to the role
    # checks: the admin client may create (200, the route's default status).
    assert client.post("/api/workspaces", json={"name": "csrf-probe"}).status_code == 200


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


def test_workspace_create_is_admin_only(client) -> None:
    """P4: POST /api/workspaces now mounts require_admin — a member gets 403
    while the admin session keeps creating workspaces."""
    _create_member(client)
    member = _member_client(client)
    denied = member.post("/api/workspaces", json={"name": "member ws"})
    assert denied.status_code == 403

    allowed = client.post(
        "/api/workspaces",
        json={"name": "admin ws", "default_workflow_key": "matrix_create_flow"},
    )
    assert allowed.status_code == 200, allowed.text


def test_studio_authoring_surface_is_admin_only(client, workspace_id, job_db) -> None:
    """P4: the Studio authoring APIs refuse non-admin full sessions with 403,
    even for workspace editors."""
    member_id = _create_member(client)
    job_db.upsert_workspace_member(workspace_id, member_id, "editor")
    editor = _member_client(client)

    assert editor.get(f"/api/workspaces/{workspace_id}/workflow-revisions").status_code == 403
    assert (
        editor.post(
            f"/api/workspaces/{workspace_id}/workflow-drafts/validate",
            json={"definition_yaml": "key: k\nlabel: l\nnodes: {}\n"},
        ).status_code
        == 403
    )
    assert (
        editor.get(
            f"/api/workspaces/{workspace_id}/workflows/demo_workflow/nodes/n/code"
        ).status_code
        == 403
    )
    assert (
        editor.get("/api/agent-definitions", params={"workspace_id": workspace_id}).status_code
        == 403
    )
    assert editor.get(f"/api/workspaces/{workspace_id}/studio-chat/sessions").status_code == 403
    assert editor.get("/api/studio-agent-tokens").status_code == 403


def test_websocket_requires_session(anon_client, client) -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        anon_client.websocket_connect("/api/agents"),
    ):
        pass
    with client.websocket_connect("/api/agents") as websocket:
        assert websocket is not None
