"""Scope enforcement contract for studio-agent scoped tokens (STUDIO-AGENT-001).

A scoped token minted for a studio chat run authenticates as the initiating
user through the Bearer channel, but every effecting endpoint (workflow
publish; node code publish/rollback/archive; agent and executor definition
publish/rollback/archive) mounts reject_studio_agent_scope and must refuse it
with 403. Draft/validate endpoints stay reachable. The endpoint inventory
below is the enumeration backstop: new effecting endpoints must be added here
together with their guard.
"""

from __future__ import annotations

from datetime import timedelta

from server.app.auth import scoped_tokens

_DRAFT_YAML = """
key: scope_guard_flow
label: Scope Guard Flow
nodes:
  clean_and_parse:
    capability: clean_and_parse
"""


def _effecting_endpoints(workspace_id: str) -> list[tuple[str, str, dict | None]]:
    node_base = f"/api/workspaces/{workspace_id}/workflows/wf/nodes/node/code"
    return [
        (
            "POST",
            f"/api/workspaces/{workspace_id}/workflow-drafts/publish",
            {"definition_yaml": _DRAFT_YAML},
        ),
        ("POST", f"{node_base}/publish", None),
        ("POST", f"{node_base}/rollback", {"version": 1}),
        ("DELETE", node_base, None),
        ("POST", "/api/agent-definitions/agent-x/publish", None),
        ("POST", "/api/agent-definitions/agent-x/rollback", {"version": 1}),
        ("DELETE", "/api/agent-definitions/agent-x", None),
        ("POST", "/api/executor-definitions/exec-x/publish", None),
        ("POST", "/api/executor-definitions/exec-x/rollback", {"version": 1}),
        ("DELETE", "/api/executor-definitions/exec-x", None),
    ]


def _scoped_client(client, job_db, *, ttl: timedelta | None = None):
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    kwargs = {"ttl": ttl} if ttl is not None else {}
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, **kwargs)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    return scoped


def test_scoped_token_authenticates_as_initiating_user(client, job_db) -> None:
    scoped = _scoped_client(client, job_db)
    response = scoped.get("/api/workspaces")
    assert response.status_code == 200


def test_scoped_token_rejected_on_all_effecting_endpoints(client, job_db) -> None:
    workspace_id = str(job_db.create_workspace("scope-guard-ws")["id"])
    scoped = _scoped_client(client, job_db)
    for method, url, payload in _effecting_endpoints(workspace_id):
        response = scoped.request(method, url, json=payload)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
        assert "Studio agent scope" in response.json()["detail"]


def test_scoped_token_allowed_on_draft_and_validate_endpoints(client, job_db) -> None:
    workspace_id = str(job_db.create_workspace("scope-draft-ws")["id"])
    scoped = _scoped_client(client, job_db)

    validate = scoped.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/validate",
        json={"definition_yaml": _DRAFT_YAML},
    )
    assert validate.status_code == 200

    # Draft writes are allowed through the scope guard; they may still fail
    # later for business reasons (no active revision, invalid payload).
    node_draft = scoped.put(
        f"/api/workspaces/{workspace_id}/workflows/wf/nodes/node/code",
        json={"code": "def run(job, job_dir, runtime):\n    pass\n"},
    )
    assert node_draft.status_code != 403

    agent_draft = scoped.put("/api/agent-definitions/agent-x/draft", json={})
    assert agent_draft.status_code != 403

    executor_draft = scoped.put("/api/executor-definitions/exec-x/draft", json={})
    assert executor_draft.status_code != 403


def test_full_session_still_reaches_effecting_endpoints(client, job_db) -> None:
    workspace_id = str(job_db.create_workspace("scope-admin-ws")["id"])
    scoped = _scoped_client(client, job_db)
    for method, url, payload in _effecting_endpoints(workspace_id):
        admin_response = client.request(method, url, json=payload)
        assert admin_response.status_code != 403, f"{method} {url}"
        scoped_response = scoped.request(method, url, json=payload)
        assert scoped_response.status_code == 403, f"{method} {url}"


_ADMIN_ENDPOINTS: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/users", None),
    ("GET", "/api/admin/connections", None),
    ("GET", "/api/admin/instance-settings", None),
    ("GET", "/api/admin/token-usage-pricing", None),
    ("PUT", "/api/admin/skill-sources/some/skill", {"ref": "main"}),
    ("POST", "/api/admin/skill-sources/relock", None),
    ("DELETE", "/api/admin/connections/conn-x", None),
]


def test_scoped_token_rejected_on_admin_endpoints(client, job_db) -> None:
    """require_admin refuses scoped identities even though the scoped token
    inherits role=admin from the initiating user's row (P0: without the
    actor_scope check the token would pass every admin endpoint)."""
    workspace_id = str(job_db.create_workspace("scope-admin-guard-ws")["id"])
    scoped = _scoped_client(client, job_db)
    endpoints = [
        *_ADMIN_ENDPOINTS,
        ("DELETE", f"/api/workspaces/{workspace_id}/members/user-x", None),
    ]
    for method, url, payload in endpoints:
        response = scoped.request(method, url, json=payload)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
    # Full admin sessions keep working.
    for method, url, payload in _ADMIN_ENDPOINTS[:4]:
        admin_response = client.request(method, url, json=payload)
        assert admin_response.status_code == 200, f"{method} {url}"


def test_expired_scoped_token_gets_401(client, job_db) -> None:
    scoped = _scoped_client(client, job_db, ttl=timedelta(seconds=-1))
    assert scoped.get("/api/workspaces").status_code == 401


def test_forged_scoped_token_gets_401(client, job_db) -> None:
    del job_db
    forged = client.__class__(client.app)
    forged.headers["authorization"] = "Bearer forged-scoped-token"
    assert forged.get("/api/workspaces").status_code == 401


def test_revoked_scoped_token_gets_401(client, job_db) -> None:
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped_tokens.revoke_scoped_token(job_db, token)
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"
    assert scoped.get("/api/workspaces").status_code == 401
