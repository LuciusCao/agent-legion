"""Shared scaffolding for the studio-agent tool-surface tests
(/api/studio-agent/tools/*), split from test_studio_agent_tools.py when it
crossed the 800-line test-file budget (#779 codex train review R3). The
sibling test files import these; the per-module autouse fixture calls
``reset_create_count`` so workspace ids stay deterministic per test.
"""

from __future__ import annotations

from server.app.auth import scoped_tokens

_WORKFLOW_KEY = "education_video_problems_generation"
_NODE_KEY = "intake_knowledge_points"

_CREATE_COUNT = 0


def reset_create_count() -> None:
    global _CREATE_COUNT
    _CREATE_COUNT = 0


def _scoped_client(client, job_db):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped, admin_id


def _create_workspace(client, name: str = "Studio Tools") -> str:
    # v62: id==key and unique per call within a test (the second workspace in
    # a test gets a suffix; TRUNCATE isolation resets the counter each test).
    # Creation no longer seeds, so publish the demo revision (which also
    # seeds the demo node codes) for the node-code/revision tools.
    global _CREATE_COUNT
    _CREATE_COUNT += 1
    ws_id = _WORKFLOW_KEY if _CREATE_COUNT == 1 else f"{_WORKFLOW_KEY}_{_CREATE_COUNT}"
    response = client.post(
        "/api/workspaces",
        json={"id": ws_id, "name": name},
    )
    assert response.status_code == 200, response.text
    from tests.helpers import publish_builtin_revision

    publish_builtin_revision(client.app.state.job_db, ws_id)
    return str(response.json()["workspace"]["id"])


def _active_yaml(scoped, workspace_id: str) -> str:
    response = scoped.get(f"/api/studio-agent/tools/workspaces/{workspace_id}/workflow/active")
    assert response.status_code == 200, response.text
    return str(response.json()["definition_yaml"])
