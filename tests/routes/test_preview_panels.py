"""Human preview panel routes: /api/workspaces/{id}/preview-panel* (issue #328).

The published read is member-level (the job detail iframe host); state/publish/
archive sit on the Studio authoring surface, and the effecting writes mount
reject_studio_agent_scope — a studio-agent scoped token can never publish
(STUDIO-AGENT-001), same split as the workflow draft store.
"""

from __future__ import annotations

from server.app.auth import scoped_tokens
from server.app.services.preview_panels import PreviewPanelService

_HTML = "<!doctype html><html><body><h1>custom panel</h1></body></html>"
_HTML_V2 = "<!doctype html><html><body><h1>custom panel v2</h1></body></html>"


def _create_workspace(client, name: str = "Preview Panels") -> str:
    response = client.post(
        "/api/workspaces", json={"id": name.lower().replace(" ", "-"), "name": name}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["workspace"]["id"])


def _scoped_client(client, job_db):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped, admin_id


def _save_draft(job_db, workspace_id: str, html: str = _HTML, created_by: str = "user:admin"):
    return PreviewPanelService(job_db).save_draft(workspace_id, html, created_by)


def test_published_read_is_null_until_published(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    response = client.get(f"/api/workspaces/{workspace_id}/preview-panel/published")
    assert response.status_code == 200
    assert response.json() == {"published": None}

    _save_draft(job_db, workspace_id)
    # A draft never leaks into the member-level published read.
    assert client.get(f"/api/workspaces/{workspace_id}/preview-panel/published").json() == {
        "published": None
    }


def test_publish_then_read_roundtrip(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    _save_draft(job_db, workspace_id)

    published = client.post(f"/api/workspaces/{workspace_id}/preview-panel/publish")
    assert published.status_code == 200, published.text
    payload = published.json()
    assert payload["status"] == "published"
    assert payload["html"] == _HTML
    assert payload["published_at"]

    read = client.get(f"/api/workspaces/{workspace_id}/preview-panel/published")
    assert read.json()["published"]["html"] == _HTML

    # State read: published set, draft consumed.
    state = client.get(f"/api/workspaces/{workspace_id}/preview-panel")
    assert state.status_code == 200
    assert state.json() == {"published": payload, "draft": None}


def test_republish_archives_previous_version(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    _save_draft(job_db, workspace_id)
    client.post(f"/api/workspaces/{workspace_id}/preview-panel/publish")
    _save_draft(job_db, workspace_id, html=_HTML_V2)

    republished = client.post(f"/api/workspaces/{workspace_id}/preview-panel/publish")

    assert republished.status_code == 200
    assert republished.json()["version"] == 2
    read = client.get(f"/api/workspaces/{workspace_id}/preview-panel/published")
    assert read.json()["published"]["html"] == _HTML_V2


def test_publish_without_draft_404(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    response = client.post(f"/api/workspaces/{workspace_id}/preview-panel/publish")
    assert response.status_code == 404


def test_archive_resets_to_builtin_fallback(client, job_db) -> None:
    workspace_id = _create_workspace(client)
    _save_draft(job_db, workspace_id)
    client.post(f"/api/workspaces/{workspace_id}/preview-panel/publish")

    archived = client.post(f"/api/workspaces/{workspace_id}/preview-panel/archive")

    assert archived.status_code == 200, archived.text
    assert archived.json() == {"published": None, "draft": None}
    assert client.get(f"/api/workspaces/{workspace_id}/preview-panel/published").json() == {
        "published": None
    }


def test_scoped_token_cannot_publish_or_archive(client, job_db) -> None:
    """STUDIO-AGENT-001: effecting preview-panel actions refuse scoped tokens."""
    workspace_id = _create_workspace(client)
    _save_draft(job_db, workspace_id, created_by="studio-agent:admin")
    scoped, _ = _scoped_client(client, job_db)

    publish = scoped.post(f"/api/workspaces/{workspace_id}/preview-panel/publish")
    assert publish.status_code == 403
    assert "cannot take effect" in publish.json()["detail"]
    archive = scoped.post(f"/api/workspaces/{workspace_id}/preview-panel/archive")
    assert archive.status_code == 403

    # Reads stay reachable: the scoped token reads state and the published row.
    assert scoped.get(f"/api/workspaces/{workspace_id}/preview-panel").status_code == 200
    published_read = scoped.get(f"/api/workspaces/{workspace_id}/preview-panel/published")
    assert published_read.status_code == 200


def test_anonymous_gets_401(anon_client, job_db) -> None:
    del job_db
    for method, url in (
        ("GET", "/api/workspaces/ws-x/preview-panel/published"),
        ("GET", "/api/workspaces/ws-x/preview-panel"),
        ("POST", "/api/workspaces/ws-x/preview-panel/publish"),
        ("POST", "/api/workspaces/ws-x/preview-panel/archive"),
    ):
        response = anon_client.request(method, url)
        assert response.status_code == 401, f"{method} {url} -> {response.status_code}"


def test_unknown_workspace_gets_404(client, job_db) -> None:
    del job_db
    assert client.get("/api/workspaces/ws-missing/preview-panel/published").status_code == 404
    assert client.get("/api/workspaces/ws-missing/preview-panel").status_code == 404
    assert client.post("/api/workspaces/ws-missing/preview-panel/publish").status_code == 404
    assert client.post("/api/workspaces/ws-missing/preview-panel/archive").status_code == 404
