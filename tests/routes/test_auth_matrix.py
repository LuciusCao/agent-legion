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


def _listed_ids(http_client) -> set[str]:
    response = http_client.get("/api/workspaces")
    assert response.status_code == 200, response.text
    return {workspace["id"] for workspace in response.json()["workspaces"]}


def _scoped_bearer_client(client, job_db, user_id: str, **mint_kwargs):
    from server.app.auth import scoped_tokens

    token = scoped_tokens.mint_scoped_token(job_db, user_id, **mint_kwargs)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped


@pytest.fixture
def two_workspaces(client, job_db) -> tuple[str, str]:
    del client
    joined = job_db.create_workspace(default_workflow_key="matrix_joined", name="Joined")["id"]
    other = job_db.create_workspace(default_workflow_key="matrix_other", name="Other")["id"]
    return str(joined), str(other)


def test_member_listing_hides_unauthorized_workspaces(client, two_workspaces, job_db) -> None:
    """#711: a non-admin only lists the workspaces it is a member of — no
    member row means an empty list, never the instance-wide enumeration."""
    joined, other = two_workspaces
    member_id = _create_member(client)
    member = _member_client(client)
    assert _listed_ids(member) == set()

    job_db.upsert_workspace_member(joined, member_id, "viewer")
    assert _listed_ids(member) == {joined}
    # Any member role counts; the role only gates reads vs writes per workspace.
    job_db.upsert_workspace_member(other, member_id, "editor")
    assert _listed_ids(member) == {joined, other}


def test_admin_listing_keeps_every_workspace(client, two_workspaces) -> None:
    """#711: admins keep the full listing without any member row (the admin
    pages — token issuance, member management — depend on it)."""
    assert _listed_ids(client) >= set(two_workspaces)


def test_studio_scoped_token_listing_follows_minter_and_binding(
    client, two_workspaces, job_db
) -> None:
    """#711: a studio-agent scoped token inherits its minter's visibility
    (unbound: membership for a member, everything for an admin), and a
    workspace-bound run token only ever lists its binding."""
    joined, other = two_workspaces
    member_id = _create_member(client)
    job_db.upsert_workspace_member(joined, member_id, "viewer")
    admin_id = str(job_db.get_user_credentials("admin")["id"])

    unbound_member = _scoped_bearer_client(client, job_db, member_id, origin="user")
    assert _listed_ids(unbound_member) == {joined}
    unbound_admin = _scoped_bearer_client(client, job_db, admin_id, origin="user")
    assert _listed_ids(unbound_admin) >= {joined, other}

    bound_admin = _scoped_bearer_client(client, job_db, admin_id, workspace_id=other)
    assert _listed_ids(bound_admin) == {other}
    # A bound token never widens its minter's visibility either.
    bound_member_elsewhere = _scoped_bearer_client(client, job_db, member_id, workspace_id=other)
    assert _listed_ids(bound_member_elsewhere) == set()


def _dashboard_filter(http_client, monkeypatch):
    """Open GET /api/dashboard/events and return the payload filter the route
    installed (None = unrestricted). TestClient cannot consume an endless SSE
    body, so the stream itself is stubbed; tests/events covers the stream."""
    from fastapi.responses import PlainTextResponse

    captured: dict = {}

    async def fake_connect(request, channel, payload_filter=None):
        captured.update(channel=channel, payload_filter=payload_filter)
        return PlainTextResponse("")

    monkeypatch.setattr(http_client.app.state.job_event_manager, "connect", fake_connect)
    response = http_client.get("/api/dashboard/events")
    assert response.status_code == 200, response.text
    assert captured["channel"] == "dashboard"
    return captured["payload_filter"]


def _dashboard_ids(payload_filter, *workspace_ids: str) -> set[str] | None:
    """Stats-batch ids a connection with ``payload_filter`` receives; None
    when the batch is dropped entirely (no empty events)."""
    import asyncio
    import json

    from server.app.events.dashboard import build_workspace_stats_batch_payload

    payload = build_workspace_stats_batch_payload(
        1, [{"id": ws, "job_stats": {}} for ws in workspace_ids]
    )
    if payload_filter is None:
        return set(workspace_ids)
    out = asyncio.run(payload_filter(payload))
    return None if out is None else {w["id"] for w in json.loads(out)["workspaces"]}


def test_dashboard_events_follow_listing_visibility(
    client, two_workspaces, job_db, monkeypatch
) -> None:
    """#881: the dashboard SSE stats stream is narrowed exactly like the #711
    listing — a non-member receives nothing, a member only its workspaces,
    admins everything; a reconnect re-resolves membership."""
    joined, other = two_workspaces
    member_id = _create_member(client)
    member = _member_client(client)

    assert _dashboard_filter(client, monkeypatch) is None  # admin: unfiltered
    outsider = _dashboard_filter(member, monkeypatch)
    assert _dashboard_ids(outsider, joined, other) is None

    job_db.upsert_workspace_member(joined, member_id, "viewer")
    reconnected = _dashboard_filter(member, monkeypatch)
    assert _dashboard_ids(reconnected, joined, other) == {joined}
    assert _dashboard_ids(reconnected, other) is None


def test_dashboard_events_scoped_tokens_follow_minter_and_binding(
    client, two_workspaces, job_db, monkeypatch
) -> None:
    joined, other = two_workspaces
    member_id = _create_member(client)
    job_db.upsert_workspace_member(joined, member_id, "viewer")
    admin_id = str(job_db.get_user_credentials("admin")["id"])

    unbound_member = _scoped_bearer_client(client, job_db, member_id, origin="user")
    assert _dashboard_ids(_dashboard_filter(unbound_member, monkeypatch), joined, other) == {joined}
    unbound_admin = _scoped_bearer_client(client, job_db, admin_id, origin="user")
    assert _dashboard_filter(unbound_admin, monkeypatch) is None
    bound_admin = _scoped_bearer_client(client, job_db, admin_id, workspace_id=other)
    assert _dashboard_ids(_dashboard_filter(bound_admin, monkeypatch), joined, other) == {other}
    bound_elsewhere = _scoped_bearer_client(client, job_db, member_id, workspace_id=other)
    assert _dashboard_ids(_dashboard_filter(bound_elsewhere, monkeypatch), joined, other) is None


def test_workspace_create_is_admin_only(client) -> None:
    """P4: POST /api/workspaces now mounts require_admin — a member gets 403
    while the admin session keeps creating workspaces."""
    _create_member(client)
    member = _member_client(client)
    denied = member.post("/api/workspaces", json={"name": "member ws"})
    assert denied.status_code == 403

    allowed = client.post(
        "/api/workspaces",
        json={"id": "matrix_create_flow", "name": "admin ws"},
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
