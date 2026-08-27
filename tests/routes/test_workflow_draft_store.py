"""GET/PUT /api/workspaces/{id}/workflow-draft (Studio YAML draft store)."""

from __future__ import annotations

_DRAFT_YAML = "key: wf\nlabel: Draft\nnodes:\n  intake:\n    capability: intake\n"


def _url(workspace_id: str) -> str:
    return f"/api/workspaces/{workspace_id}/workflow-draft"


def test_get_returns_structured_empty_state(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-empty", default_workflow_key="wf")

    response = client.get(_url(workspace["id"]))

    assert response.status_code == 200
    assert response.json() == {"definition_yaml": None, "updated_at": None}


def test_put_then_get_roundtrip(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store", default_workflow_key="wf")

    put = client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})

    assert put.status_code == 200
    assert put.json()["definition_yaml"] == _DRAFT_YAML
    assert put.json()["updated_at"]
    got = client.get(_url(workspace["id"]))
    assert got.json()["definition_yaml"] == _DRAFT_YAML
    assert got.json()["updated_at"] == put.json()["updated_at"]


def test_put_overwrites_the_previous_draft(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-overwrite", default_workflow_key="wf")
    client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})

    updated = client.put(_url(workspace["id"]), json={"definition_yaml": "key: wf\nlabel: V2\n"})

    assert updated.status_code == 200
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] == "key: wf\nlabel: V2\n"


def test_put_rejects_blank_draft(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-blank", default_workflow_key="wf")

    for blank in ("", "   \n  "):
        response = client.put(_url(workspace["id"]), json={"definition_yaml": blank})
        assert response.status_code == 422
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] is None


def test_unknown_workspace_gets_404(client) -> None:
    assert client.get(_url("no-such-ws")).status_code == 404
    put = client.put(_url("no-such-ws"), json={"definition_yaml": _DRAFT_YAML})
    assert put.status_code == 404


def test_drafts_are_isolated_between_workspaces(client, job_db) -> None:
    first = job_db.create_workspace("ws-store-a", default_workflow_key="wf")
    second = job_db.create_workspace("ws-store-b", default_workflow_key="wf")

    client.put(_url(first["id"]), json={"definition_yaml": _DRAFT_YAML})

    assert client.get(_url(second["id"])).json() == {
        "definition_yaml": None,
        "updated_at": None,
    }


def test_anonymous_gets_401(anon_client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-anon", default_workflow_key="wf")

    assert anon_client.get(_url(workspace["id"])).status_code == 401
    put = anon_client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    assert put.status_code == 401


def test_cookie_put_without_csrf_header_gets_403(client, job_db) -> None:
    workspace = job_db.create_workspace("ws-store-csrf", default_workflow_key="wf")
    bare = client.__class__(client.app)
    session = client.cookies.get("agent_legion_session")
    assert session
    bare.cookies.set("agent_legion_session", session)

    response = bare.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})

    assert response.status_code == 403
    assert client.get(_url(workspace["id"])).json()["definition_yaml"] is None


def test_scoped_token_cannot_write_but_can_read(client, job_db) -> None:
    """STUDIO-AGENT-001: PUT mounts reject_studio_agent_scope (the mechanical
    inventory in test_studio_agent_scope.py pins this); the GET stays readable
    like the other studio_secured reads."""
    from server.app.auth import scoped_tokens

    workspace = job_db.create_workspace("ws-store-scoped", default_workflow_key="wf")
    client.put(_url(workspace["id"]), json={"definition_yaml": _DRAFT_YAML})
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {scoped_tokens.mint_scoped_token(job_db, admin_id)}"

    put = scoped.put(_url(workspace["id"]), json={"definition_yaml": "key: evil\n"})
    assert put.status_code == 403
    assert "Studio agent scope" in put.json()["detail"]
    got = scoped.get(_url(workspace["id"]))
    assert got.status_code == 200
    assert got.json()["definition_yaml"] == _DRAFT_YAML
