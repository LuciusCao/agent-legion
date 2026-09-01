"""Studio-agent preview panel tool endpoints (issue #328).

Behavioral contract for /api/studio-agent/tools/workspaces/{id}/preview/*:
scoped tokens only (the scope matrix itself lives in test_studio_agent_tools.py
through ``_tool_endpoints``); drafts carry the ``studio-agent:<user>``
attribution; validation rejects non-HTML bundles; publish stays unreachable
from the tool surface (STUDIO-AGENT-001, covered by test_preview_panels.py).
"""

from __future__ import annotations

from server.app.auth import scoped_tokens

_HTML = "<!doctype html><html><body><h1>agent panel</h1></body></html>"


def _create_workspace(client, name: str = "Preview Tools") -> str:
    response = client.post(
        "/api/workspaces", json={"id": name.lower().replace(" ", "-"), "name": name}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["workspace"]["id"])


def _scoped_client(client, job_db, workspace_id: str | None = None):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=workspace_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped, admin_id


def test_save_draft_attributes_studio_agent(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, admin_id = _scoped_client(client, job_db)

    saved = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/preview/panel/draft",
        json={"html": _HTML, "change_note": "agent draft"},
    )

    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["status"] == "draft"
    assert payload["created_by"] == f"studio-agent:{admin_id}"
    assert payload["html"] == _HTML

    state = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/preview/panel")
    assert state.status_code == 200
    assert state.json()["draft"]["version"] == payload["version"]
    assert state.json()["published"] is None


def test_save_draft_rejects_invalid_html(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    response = scoped.put(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/preview/panel/draft",
        json={"html": "definitely not a document"},
    )

    assert response.status_code == 400
    assert "HTML" in response.json()["detail"]


def test_preview_context_empty_workspace(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    scoped, _ = _scoped_client(client, job_db)

    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/preview/context")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["workspace_id"] == workspace_id
    assert payload["recent_jobs"] == []
    assert payload["selected_job"] is None
    assert payload["samples"] == {}


def test_preview_context_unknown_workspace_404(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get("/api/studio-agent/tools/workspaces/ws-missing/preview/context")
    assert response.status_code == 404


def test_panel_state_unknown_workspace_404(client, job_db) -> None:
    scoped, _ = _scoped_client(client, job_db)
    response = scoped.get("/api/studio-agent/tools/workspaces/ws-missing/preview/panel")
    assert response.status_code == 404


def test_workspace_bound_token_is_refused_on_other_workspaces(client, job_db) -> None:
    workspace_id = _create_workspace(client, "Preview Bound A")
    other_id = _create_workspace(client, "Preview Bound B")
    scoped, _ = _scoped_client(client, job_db, workspace_id=workspace_id)

    assert (
        scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/preview/panel").status_code
        == 200
    )
    response = scoped.get(f"/api/studio-agent/tools/workspaces/{other_id}/preview/panel")
    assert response.status_code == 403
    assert "bound" in response.json()["detail"]
