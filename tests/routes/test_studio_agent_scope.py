"""Scope enforcement contract for studio-agent scoped tokens (STUDIO-AGENT-001).

A scoped token minted for a studio chat run authenticates as the initiating
user through the Bearer channel, but every effecting endpoint — workflow
publish; node code publish/rollback/archive; agent and executor definition
publish/rollback/archive; job lifecycle and execution triggers (delete,
rerun, run-to, continue, batch intake, workflow upgrade, replay); workspace,
secret, package, member and settings writes; worker pause/resume — mounts
reject_studio_agent_scope and must refuse it with 403, and require_admin
refuses scoped identities outright. Studio chat effecting endpoints (session
create/close, message send/cancel, permission answers) are likewise guarded:
a scoped token must not mint fresh run tokens nor self-approve permission
prompts. Draft/validate endpoints stay reachable.

_ENDPOINT_INVENTORY below is the single source of truth, enforced two ways:
the HTTP-level tests assert 403 for every enumerated endpoint, and
test_every_write_endpoint_guarded_or_explicitly_exempt walks the app's route
table so a new non-GET endpoint turns red until it is either added here with
its guard or explicitly exempted with a reason.
"""

from __future__ import annotations

import re
from datetime import timedelta

from server.app.auth import scoped_tokens

_DRAFT_YAML = """
key: scope_guard_flow
label: Scope Guard Flow
nodes:
  clean_items:
    capability: clean_items
"""

_NODE_CODE = "/api/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code"
_CHAT = "/api/workspaces/{workspace_id}/studio-chat"

# Effecting write routes (path templates as they appear in the app's route
# table). Every entry must mount reject_studio_agent_scope — the mechanical
# inventory test at the bottom asserts exactly that, in both directions.
_EFFECTING_WRITE_ROUTES: list[tuple[str, str, dict | None]] = [
    (
        "POST",
        "/api/workspaces/{workspace_id}/workflow-drafts/publish",
        {"definition_yaml": _DRAFT_YAML},
    ),
    ("POST", f"{_NODE_CODE}/publish", None),
    ("POST", f"{_NODE_CODE}/rollback", {"version": 1}),
    ("DELETE", _NODE_CODE, None),
    ("POST", "/api/agent-definitions/{agent_id}/publish", None),
    ("POST", "/api/agent-definitions/{agent_id}/rollback", {"version": 1}),
    ("DELETE", "/api/agent-definitions/{agent_id}", None),
    ("POST", "/api/executor-definitions/{executor_id}/publish", None),
    ("POST", "/api/executor-definitions/{executor_id}/rollback", {"version": 1}),
    ("DELETE", "/api/executor-definitions/{executor_id}", None),
    # Job lifecycle and execution triggers (P0-1/P1-1).
    ("DELETE", "/api/jobs/{job_id}", None),
    ("DELETE", "/api/workspaces/{workspace_id}/jobs/batch", None),
    ("POST", "/api/jobs/{job_id}/nodes/{node_key}/rerun", None),
    ("POST", "/api/jobs/{job_id}/run-to", None),
    ("POST", "/api/jobs/{job_id}/continue", None),
    ("POST", "/api/workspaces/{workspace_id}/jobs/batch-rerun", None),
    ("POST", "/api/workspaces/{workspace_id}/jobs/batch-run-to", None),
    ("POST", "/api/workspaces/{workspace_id}/jobs/rerun-by-failure", None),
    ("POST", "/api/workspaces/{workspace_id}/job-batches", None),
    ("POST", "/api/jobs/{job_id}/upgrade-workflow", None),
    # Workspace, secret, package, member and settings writes.
    ("POST", "/api/workspaces", None),
    ("PATCH", "/api/workspaces/{workspace_id}", None),
    ("DELETE", "/api/workspaces/{workspace_id}", None),
    ("PUT", "/api/workspaces/{workspace_id}/secrets/{name}", None),
    ("DELETE", "/api/workspaces/{workspace_id}/secrets/{name}", None),
    ("DELETE", "/api/workspaces/{workspace_id}/packages/{package_id:int}", None),
    ("PATCH", "/api/workspaces/{workspace_id}/packages/{package_id:int}", None),
    ("POST", "/api/workspaces/{workspace_id}/jobs/package", None),
    ("POST", "/api/workspaces/{workspace_id}/jobs/clear-packed", None),
    ("DELETE", "/api/workspaces/{workspace_id}/members/{user_id}", None),
    ("PATCH", "/api/workspaces/{workspace_id}/settings/{section}", None),
    ("PUT", "/api/workspaces/{workspace_id}/configuration", None),
    # Quality review writes and replays.
    ("POST", "/api/workspaces/{workspace_id}/quality/sample-batches", None),
    ("POST", "/api/workspaces/{workspace_id}/quality/sample-items/{item_id}/labels", None),
    ("POST", "/api/workspaces/{workspace_id}/quality/sample-items/{item_id}/replays", None),
    # Worker scheduling control.
    ("POST", "/api/worker/pause", None),
    ("POST", "/api/worker/resume", None),
    # Studio agent token lifecycle: minting/revoking automation tokens is an
    # effecting write (a scoped token must not mint itself a sibling token).
    ("POST", "/api/studio-agent-tokens", None),
    ("DELETE", "/api/studio-agent-tokens/{token_id}", None),
    # Studio chat effecting endpoints: session lifecycle (create mints a
    # fresh scoped token), message send/cancel, and permission answers
    # (self-approval would void the human-confirmation boundary).
    ("POST", f"{_CHAT}/sessions", {"agent_id": "agent-x"}),
    ("DELETE", f"{_CHAT}/sessions/{{session_id}}", None),
    ("POST", f"{_CHAT}/sessions/{{session_id}}/messages", {"text": "hi"}),
    ("POST", f"{_CHAT}/sessions/{{session_id}}/cancel", None),
    ("POST", f"{_CHAT}/sessions/{{session_id}}/permissions/allow-all", {"enabled": True}),
    ("POST", f"{_CHAT}/sessions/{{session_id}}/permissions/{{request_id}}", {"deny": True}),
    # Context push (Studio node selection): the agent reads it back via
    # get_studio_context, so a scoped token must not rewrite its own context.
    ("PUT", f"{_CHAT}/sessions/{{session_id}}/context", {"selected_node_key": "n"}),
]

# Unguarded non-GET routes, each with the reason a scoped token may reach it.
# A new write endpoint that is neither in _EFFECTING_WRITE_ROUTES nor here
# fails the inventory test — classify it explicitly.
_EXEMPT_WRITE_ROUTES: dict[tuple[str, str], str] = {
    # Public auth lifecycle (AGENTS.md: only health/login/bootstrap are public).
    ("POST", "/api/auth/login"): "public login",
    ("POST", "/api/auth/bootstrap"): "public bootstrap",
    ("POST", "/api/auth/logout"): "session teardown, no platform effect",
    # require_admin refuses scoped identities outright (actor_scope check).
    ("POST", "/api/users"): "require_admin",
    ("PATCH", "/api/users/{user_id}"): "require_admin",
    ("PUT", "/api/workspaces/{workspace_id}/members"): "require_admin",
    ("POST", "/api/workflows"): "require_admin",
    ("PUT", "/api/admin/token-usage-pricing"): "require_admin",
    ("PUT", "/api/admin/instance-settings"): "require_admin",
    ("PUT", "/api/admin/skill-sources/{skill_key:path}"): "require_admin",
    ("POST", "/api/admin/skill-sources/relock"): "require_admin",
    ("POST", "/api/admin/connections"): "require_admin",
    ("PUT", "/api/admin/connections/{key}"): "require_admin",
    ("DELETE", "/api/admin/connections/{key}"): "require_admin",
    ("POST", "/api/admin/connections/{key}/test"): "require_admin",
    ("PUT", "/api/admin/studio-agents"): "require_admin",
    ("POST", "/api/agent-register-tokens"): "require_admin",
    ("POST", "/api/agent-register-tokens/{token_id}/revoke"): "require_admin",
    ("POST", "/api/agent-workers/{worker_id}/revoke"): "require_admin",
    # Worker credential channel (x-agent-worker-token / register token, not a
    # user session; scoped Bearer tokens never authenticate here).
    ("POST", "/api/agent-workers/register"): "worker register-token channel",
    ("POST", "/api/agent-executions/claim"): "worker credential channel",
    ("POST", "/api/agent-executions/{execution_id}/heartbeat"): "worker credential channel",
    ("POST", "/api/agent-executions/{execution_id}/release-slot"): "worker credential channel",
    ("POST", "/api/agent-executions/{execution_id}/result"): "worker credential channel",
    ("POST", "/api/artifacts"): "worker credential channel",
    # Draft/validate/compare: no production effect; explicitly reachable for
    # scoped tokens per STUDIO-AGENT-001.
    ("POST", "/api/workspaces/{workspace_id}/workflow-drafts/validate"): "validate only",
    ("POST", "/api/workspaces/{workspace_id}/workflow-drafts/compare"): "read-only compare",
    ("PUT", f"{_NODE_CODE}"): "node code draft write",
    ("POST", "/api/agent-definitions"): "creates a draft",
    ("PUT", "/api/agent-definitions/{agent_id}/draft"): "draft write",
    ("POST", "/api/agent-definitions/{agent_id}/copy"): "creates a draft",
    ("POST", "/api/executor-definitions"): "creates a draft",
    ("PUT", "/api/executor-definitions/{executor_id}/draft"): "draft write",
    ("POST", "/api/executor-definitions/{executor_id}/copy"): "creates a draft",
    ("POST", "/api/skills/validate"): "validate only",
    ("POST", "/api/workspaces/{workspace_id}/jobs/batch-rerun/preview"): "preview only",
    # Scoped-only tool surface (require_studio_agent_scope): these endpoints
    # exist FOR the scoped token and are draft/validate/register-by-design.
    (
        "POST",
        "/api/studio-agent/tools/workspaces/{workspace_id}/workflow/validate",
    ): "scoped-only tool surface",
    (
        "POST",
        "/api/studio-agent/tools/workspaces/{workspace_id}/workflow/compare",
    ): "scoped-only tool surface",
    (
        "PUT",
        "/api/studio-agent/tools/workspaces/{workspace_id}/workflows/{workflow_key}/nodes/{node_key}/code/draft",
    ): "scoped-only tool surface",
    (
        "PUT",
        "/api/studio-agent/tools/agent-definitions/{agent_id}/draft",
    ): "scoped-only tool surface",
    ("POST", "/api/studio-agent/tools/workflows/register"): "scoped-only tool surface",
    # SPA mount's API 404 catch-all (server/app/spa.py).
    ("POST", "/api/{path:path}"): "API 404 catch-all",
    ("PUT", "/api/{path:path}"): "API 404 catch-all",
    ("PATCH", "/api/{path:path}"): "API 404 catch-all",
    ("DELETE", "/api/{path:path}"): "API 404 catch-all",
}

_PATH_PARAM_VALUES = {
    "workflow_key": "wf",
    "node_key": "node",
    "agent_id": "agent-x",
    "executor_id": "exec-x",
    "job_id": "job-x",
    "item_id": "item-x",
    "user_id": "user-x",
    "name": "secret-x",
    "package_id": "1",
    "section": "agent",
    "token_id": "token-x",
    "session_id": "session-x",
    "request_id": "request-x",
}


def _effecting_endpoints(workspace_id: str) -> list[tuple[str, str, dict | None]]:
    values = {**_PATH_PARAM_VALUES, "workspace_id": workspace_id}
    return [
        (
            method,
            re.sub(r"\{(\w+)(?::\w+)?\}", lambda match: values[match.group(1)], template),
            payload,
        )
        for method, template, payload in _EFFECTING_WRITE_ROUTES
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
    workspace_id = str(
        job_db.create_workspace(default_workflow_key="demo_workflow", name="scope-guard-ws")["id"]
    )
    scoped = _scoped_client(client, job_db)
    for method, url, payload in _effecting_endpoints(workspace_id):
        response = scoped.request(method, url, json=payload)
        assert response.status_code == 403, f"{method} {url} -> {response.status_code}"
        assert "Studio agent scope" in response.json()["detail"]


def test_scoped_token_allowed_on_draft_and_validate_endpoints(client, job_db) -> None:
    workspace_id = str(
        job_db.create_workspace(default_workflow_key="demo_workflow", name="scope-draft-ws")["id"]
    )
    scoped = _scoped_client(client, job_db)

    validate = scoped.post(
        f"/api/workspaces/{workspace_id}/workflow-drafts/validate",
        json={"definition_yaml": _DRAFT_YAML},
    )
    assert validate.status_code == 200

    # Draft writes are allowed through the scope guard; they may still fail
    # later for business reasons (no active revision, invalid payload) — but a
    # 5xx here would be a server bug masquerading as a pass.
    node_draft = scoped.put(
        f"/api/workspaces/{workspace_id}/workflows/wf/nodes/node/code",
        json={"code": "def run(job, job_dir, runtime):\n    pass\n"},
    )
    assert node_draft.status_code not in (401, 403) and node_draft.status_code < 500

    agent_draft = scoped.put("/api/agent-definitions/agent-x/draft", json={})
    assert agent_draft.status_code not in (401, 403) and agent_draft.status_code < 500

    executor_draft = scoped.put("/api/executor-definitions/exec-x/draft", json={})
    assert executor_draft.status_code not in (401, 403) and executor_draft.status_code < 500


def test_unknown_scope_type_is_also_rejected_on_effecting_endpoints(client, job_db) -> None:
    """reject_studio_agent_scope refuses any non-empty actor_scope, aligned
    with require_admin: a future scope type must not silently inherit
    effecting rights."""
    workspace_id = str(
        job_db.create_workspace(default_workflow_key="demo_workflow", name="scope-future-ws")["id"]
    )
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id, scope="future_scope")
    scoped = client.__class__(client.app)
    scoped.headers["authorization"] = f"Bearer {token}"

    response = scoped.post("/api/worker/pause", params={"workspace_id": workspace_id})
    assert response.status_code == 403
    assert "Scoped tokens cannot take effect" in response.json()["detail"]


def test_full_session_still_reaches_effecting_endpoints(client, job_db) -> None:
    workspace_id = str(
        job_db.create_workspace(default_workflow_key="demo_workflow", name="scope-admin-ws")["id"]
    )
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
    workspace_id = str(
        job_db.create_workspace(default_workflow_key="demo_workflow", name="scope-admin-guard-ws")[
            "id"
        ]
    )
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


def test_workflow_register_tool_requires_admin_user(client, job_db) -> None:
    """The studio-agent register tool creates a platform-global workflow key,
    so it aligns with the human-facing POST /api/workflows (require_admin):
    scoped tokens minted for non-admin users get 403, admin tokens pass."""
    member_id = str(job_db.create_user("scope-member", password_hash=None)["id"])
    member_token = scoped_tokens.mint_scoped_token(job_db, member_id)
    member_scoped = client.__class__(client.app)
    member_scoped.headers["authorization"] = f"Bearer {member_token}"
    url = "/api/studio-agent/tools/workflows/register"
    payload = {"key": "scope_register_flow", "label": "Scope Register Flow"}

    denied = member_scoped.post(url, json=payload)
    assert denied.status_code == 403

    scoped = _scoped_client(client, job_db)
    allowed = scoped.post(url, json=payload)
    assert allowed.status_code == 200, allowed.text


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


def _write_route_dependencies(app) -> dict[tuple[str, str], list[str]]:
    """{(method, path_template): dependency names} for every non-GET route.

    The dependant tree is flattened so endpoint-signature Depends (e.g.
    ``_guard: Depends(reject_studio_agent_scope)``) count exactly like
    router-level mounts.
    """
    routes: dict[tuple[str, str], list[str]] = {}
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        names: list[str] = []
        stack = [dependant]
        while stack:
            node = stack.pop()
            if node.call is not None:
                names.append(getattr(node.call, "__name__", str(node.call)))
            stack.extend(node.dependencies)
        for method in methods - {"GET", "HEAD", "OPTIONS"}:
            routes[(method, route.path)] = names
    return routes


def test_every_write_endpoint_guarded_or_explicitly_exempt(client) -> None:
    """Mechanical backstop for the enumeration above: a new non-GET endpoint
    turns this red until it is classified — either it mounts
    reject_studio_agent_scope and joins _EFFECTING_WRITE_ROUTES, or it is
    added to _EXEMPT_WRITE_ROUTES with a reason. Both assertions are exact
    set equalities, so a dropped guard or a stale entry also fails."""
    routes = _write_route_dependencies(client.app)
    guarded = {key for key, names in routes.items() if "reject_studio_agent_scope" in names}
    effecting = {(method, template) for method, template, _ in _EFFECTING_WRITE_ROUTES}
    assert guarded == effecting
    assert set(routes) - guarded == set(_EXEMPT_WRITE_ROUTES)
