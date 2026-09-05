"""Shared fixtures for the studio publish-request route tests (#416/#429).

Used by tests/routes/test_studio_publish_requests.py (behavioral contract)
and tests/routes/test_studio_publish_request_races.py (concurrency pins) —
the split kept both files under the 1000-line test budget, so the seeding
helpers live here once.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.app.auth import scoped_tokens
from server.app.services.node_codes import NodeCodeService

DRAFT_YAML = """
key: publish_flow_ws
label: Publish Flow
nodes:
  do_thing:
    capability: do_thing
"""


def seed_workspace(client: TestClient, job_db, name: str = "publish_flow_ws") -> str:
    """Create a workspace via the API (id == key, v62) and publish v1 so the
    draft has a resolvable baseline + node code."""
    response = client.post("/api/workspaces", json={"id": name, "name": "Publish WS"})
    assert response.status_code == 200, response.text
    workspace_id = str(response.json()["workspace"]["id"])
    codes = NodeCodeService(job_db.dsn_identity)
    codes.save_draft(
        workspace_id,
        name,
        "do_thing",
        "def run(job, job_dir, runtime):\n    pass\n",
        "test seed",
    )
    codes.publish(workspace_id, name, "do_thing")
    yaml = DRAFT_YAML if name == "publish_flow_ws" else DRAFT_YAML.replace("publish_flow_ws", name)
    published = client.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/publish",
        json={"definition_yaml": yaml},
    )
    assert published.status_code == 200 and published.json()["valid"], published.text
    return workspace_id


def scoped_client(client, job_db, workspace_id: str | None = None) -> TestClient:
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, workspace_id=workspace_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped


def put_draft(client: TestClient, workspace_id: str, yaml: str) -> None:
    """Seed the workspace draft store (the YAML confirm publishes)."""
    response = client.put(
        f"/api/workspaces/{workspace_id}/workflow-draft",
        json={"definition_yaml": yaml},
    )
    assert response.status_code == 200, response.text


def request_publish(scoped: TestClient, workspace_id: str):
    return scoped.post(
        f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/publish-request"
    )


def pending(client: TestClient, workspace_id: str):
    return client.get(f"/api/workspaces/{workspace_id}/workflow-drafts/publish-request")
