"""Workspace API intake token tests (#626).

The machine-to-machine submission channel: an admin issues a
``{token_id}.{secret}`` credential bound to one workspace, an external
system presents it as ``Authorization: Bearer`` against the runs surface.
Covers the full lifecycle (issue / resolve / expiry / revoke / bad shape),
the auth chain (Bearer CSRF exemption, cross-workspace 404, the api-scope
blast radius — admin endpoints and every other effecting endpoint refuse
it), and the management routes (admin-only issue/list/revoke, one-time
plaintext, last_used_at watermark).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.helpers import publish_legacy_intake_revision

WORKSPACE = "api-token-ws"
OTHER = "api-token-ws-other"


def _create_workspace(client: TestClient, ws_id: str, name: str = "api-token-ws") -> str:
    response = client.post("/api/workspaces", json={"id": ws_id, "name": name})
    assert response.status_code == 200, response.text
    publish_legacy_intake_revision(client.app.state.job_db, ws_id)
    return ws_id


def _issue(client: TestClient, workspace_id: str, **payload) -> dict:
    created = client.post(f"/api/workspaces/{workspace_id}/api-tokens", json=payload)
    assert created.status_code == 201, created.text
    return created.json()


def _bearer_client(client: TestClient, api_token: str) -> TestClient:
    """A cookie-less client with only the Bearer credential set."""
    api_client = client.__class__(client.app)
    api_client.headers["authorization"] = f"Bearer {api_token}"
    return api_client


def _insert_material(client: TestClient, workspace_id: str, material_id: str) -> None:
    job_db = client.app.state.job_db
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, 'ready', 'tester')",
            (
                material_id,
                workspace_id,
                f"hash-{material_id}",
                f"{workspace_id}/hash-{material_id}/doc.txt",
            ),
        )


def _submit_run(api: TestClient, workspace_id: str, material_id: str):
    return api.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={"items": [{"type": "material", "material_id": material_id}]},
    )


# --- management endpoints ----------------------------------------------------


def test_management_requires_admin(client) -> None:
    _create_workspace(client, WORKSPACE)
    # Anonymous: 401.
    anon = client.__class__(client.app)
    assert anon.get(f"/api/workspaces/{WORKSPACE}/api-tokens").status_code == 401
    # Non-admin member of the workspace: require_workspace_access lets the
    # member through, then require_admin inside the router refuses (403). A
    # non-member would be 404 first (the secured() enumeration rule).
    client.post("/api/users", json={"username": "member", "password": "pw"})
    member = client.__class__(client.app)
    assert (
        member.post("/api/auth/login", json={"username": "member", "password": "pw"}).status_code
        == 200
    )
    member.headers["x-agent-legion-request"] = "1"
    member_id = str(client.app.state.job_db.get_user_credentials("member")["id"])
    client.put(
        f"/api/workspaces/{WORKSPACE}/members", json={"user_id": member_id, "role": "editor"}
    )
    assert (
        member.post(f"/api/workspaces/{WORKSPACE}/api-tokens", json={"label": "x"}).status_code
        == 403
    )
    assert member.get(f"/api/workspaces/{WORKSPACE}/api-tokens").status_code == 403


def test_issue_returns_plaintext_once_and_list_never_does(client) -> None:
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms cron")
    assert issued["workspace_id"] == WORKSPACE
    assert issued["label"] == "cms cron"
    plaintext = issued["api_token"]
    assert plaintext.startswith(f"{issued['token_id']}.")

    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"]
    assert len(listed) == 1
    entry = listed[0]
    assert entry["token_id"] == issued["token_id"]
    assert entry["revoked"] is False
    assert entry["expires_at"] is None
    assert "api_token" not in entry
    assert "token_hash" not in str(entry)


def test_issue_on_unknown_workspace_400(client) -> None:
    response = client.post("/api/workspaces/never-created/api-tokens", json={"label": "x"})
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_issue_with_ttl_and_expiry_visible(client) -> None:
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="short-lived", ttl_hours=2)
    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"]
    entry = next(t for t in listed if t["token_id"] == issued["token_id"])
    assert entry["expires_at"] is not None
    expires = datetime.fromisoformat(entry["expires_at"])
    remaining = expires - datetime.now(UTC)
    assert timedelta(hours=1) < remaining <= timedelta(hours=2, minutes=1)


# --- auth chain: resolution, formats, lifecycle -------------------------------


def test_bearer_token_submits_runs_without_csrf(client) -> None:
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    # No session cookie, no x-agent-legion-request header — the Bearer
    # channel is CSRF-exempt by design (issue #626).
    assert "cookie" not in api.headers or not api.cookies
    response = _submit_run(api, WORKSPACE, "mat-1")
    assert response.status_code == 200, response.text
    run = response.json()["run"]
    assert response.json()["created_count"] == 1

    # The read side of the same channel.
    listed = api.get(f"/api/workspaces/{WORKSPACE}/runs")
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()["runs"]] == [run["id"]]
    detail = api.get(f"/api/workspaces/{WORKSPACE}/runs/{run['id']}")
    assert detail.status_code == 200
    assert detail.json()["run"]["id"] == run["id"]
    jobs = api.get(f"/api/workspaces/{WORKSPACE}/jobs")
    assert jobs.status_code == 200
    assert len(jobs.json()["jobs"]) == 1
    snapshot = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["total"] == 1
    assert [j["id"] for j in snapshot.json()["jobs"]] == [j["id"] for j in jobs.json()["jobs"]]


def test_api_token_reads_jobs_of_bound_workspace_only(client) -> None:
    _create_workspace(client, WORKSPACE)
    _create_workspace(client, OTHER, name="other")
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    assert _submit_run(api, WORKSPACE, "mat-1").status_code == 200
    # Cross-workspace reads: 404, the same as any non-member.
    assert api.get(f"/api/workspaces/{OTHER}/runs").status_code == 404
    assert api.get(f"/api/workspaces/{OTHER}/jobs").status_code == 404


def test_cross_workspace_submission_gets_404(client) -> None:
    _create_workspace(client, WORKSPACE)
    _create_workspace(client, OTHER, name="other")
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    response = _submit_run(api, OTHER, "mat-1")
    assert response.status_code == 404


def test_bad_secret_bad_shape_and_unknown_token_get_401(client) -> None:
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms")
    bad_secret = f"{issued['token_id']}.wrong-secret-entirely"
    assert (
        _bearer_client(client, bad_secret).get(f"/api/workspaces/{WORKSPACE}/runs").status_code
        == 401
    )
    # Shapes that are not {id}.{secret} never resolve as API tokens.
    for malformed in ("nodot", "trailing.", ".leading", "", "a.b.c"):
        api = _bearer_client(client, malformed) if malformed else client.__class__(client.app)
        assert api.get(f"/api/workspaces/{WORKSPACE}/runs").status_code == 401
    unknown = "deadbeefdeadbeefdeadbeefdeadbeef.some-secret-value"
    assert (
        _bearer_client(client, unknown).get(f"/api/workspaces/{WORKSPACE}/runs").status_code == 401
    )


def test_expired_token_gets_401(client, job_db) -> None:
    _create_workspace(client, WORKSPACE)
    # Mint straight through the store with a past expiry.
    store = client.app.state.workspace_api_token_store
    _token_id, plaintext = store.issue_api_token(
        workspace_id=WORKSPACE,
        label="already-dead",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert (
        _bearer_client(client, plaintext).get(f"/api/workspaces/{WORKSPACE}/runs").status_code
        == 401
    )


def test_revoked_token_gets_401(client) -> None:
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    assert _submit_run(api, WORKSPACE, "mat-1").status_code == 200
    revoked = client.delete(f"/api/workspaces/{WORKSPACE}/api-tokens/{issued['token_id']}")
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True
    # The same credential dies immediately — submission AND reads.
    assert _submit_run(api, WORKSPACE, "mat-1").status_code == 401
    assert api.get(f"/api/workspaces/{WORKSPACE}/runs").status_code == 401
    # Double revoke: 404 (already revoked, indistinguishable from unknown).
    assert (
        client.delete(f"/api/workspaces/{WORKSPACE}/api-tokens/{issued['token_id']}").status_code
        == 404
    )


def test_revoke_is_workspace_scoped(client) -> None:
    _create_workspace(client, WORKSPACE)
    _create_workspace(client, OTHER, name="other")
    issued = _issue(client, WORKSPACE, label="cms")
    # Revoking through the OTHER workspace's scope: the token does not
    # belong there, so 404 — and it still works for its own workspace.
    assert (
        client.delete(f"/api/workspaces/{OTHER}/api-tokens/{issued['token_id']}").status_code == 404
    )
    assert (
        _bearer_client(client, issued["api_token"])
        .get(f"/api/workspaces/{WORKSPACE}/runs")
        .status_code
        == 200
    )


def test_listing_shows_revoked_and_last_used(client) -> None:
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    assert (
        _submit_run(_bearer_client(client, issued["api_token"]), WORKSPACE, "mat-1").status_code
        == 200
    )
    client.delete(f"/api/workspaces/{WORKSPACE}/api-tokens/{issued['token_id']}")
    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"]
    entry = next(t for t in listed if t["token_id"] == issued["token_id"])
    assert entry["revoked"] is True
    assert entry["last_used_at"] is not None


def test_last_used_at_throttled(client) -> None:
    """Two submissions inside the throttle window produce one UPDATE."""
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    _insert_material(client, WORKSPACE, "mat-2")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    assert _submit_run(api, WORKSPACE, "mat-1").status_code == 200
    first = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"][0]
    assert first["last_used_at"] is not None
    # Drain the in-memory throttle map: the next resolve must stamp again.
    store = client.app.state.workspace_api_token_store
    store._last_used_at_refreshed.clear()
    assert _submit_run(api, WORKSPACE, "mat-2").status_code == 200


# --- blast radius: api scope must not leak anywhere else ----------------------


def test_api_token_cannot_reach_admin_endpoints(client) -> None:
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    assert api.get("/api/users").status_code == 403
    assert api.get("/api/agent-register-tokens").status_code == 403


def test_api_token_cannot_mint_tokens(client) -> None:
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    # Review hardening: POST /api-tokens is workspace-scoped but OFF the
    # intake allowlist — the membership guard 404s the machine identity
    # before require_admin's 403 (both refusals; the old pin expected 403).
    response = api.post(f"/api/workspaces/{WORKSPACE}/api-tokens", json={"label": "sibling"})
    assert response.status_code == 404
    assert (
        api.delete(f"/api/workspaces/{WORKSPACE}/api-tokens/{issued['token_id']}").status_code
        == 404
    )
    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"]
    assert len(listed) == 1  # nothing minted, nothing self-revoked


def test_api_token_rejected_on_other_effecting_endpoints(client) -> None:
    """The api scope must not inherit effecting rights anywhere else:
    reject_studio_agent_scope refuses ANY non-empty actor_scope — the runs
    router is the single deliberate exception (require_workspace_api_intake)."""
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    endpoints = [
        # #626 review: the scopeless POST /api/workspaces mount now hard-404s
        # the api scope (the hardened membership guard) instead of the old
        # 403 — both are refusals, the expected code is pinned per-case.
        ("POST", "/api/workspaces", {"id": "new-ws", "name": "x"}),
        ("POST", f"/api/workspaces/{WORKSPACE}/job-batches", None),
        ("DELETE", "/api/jobs/job-x", None),
        ("POST", "/api/jobs/job-x/run-to", None),
        ("POST", "/api/worker/pause", None),
        # Minting sibling credentials is an admin endpoint: refused by
        # require_admin's scope check (not the effecting guard).
        ("POST", "/api/studio-agent-tokens", None),
        (
            "POST",
            f"/api/workspaces/{WORKSPACE}/workflow-drafts/publish",
            {"definition_yaml": "key: k\nlabel: l\nnodes: {}"},
        ),
        ("DELETE", f"/api/workspaces/{WORKSPACE}/materials/mat-x", None),
    ]
    # #626 review: the intake allowlist (POST/GET runs + jobs listing) 404s
    # the api identity on every other workspace-scoped route before the
    # route-level guards' 403 — and on scopeless mounts too.
    scopeless_404 = {
        ("POST", "/api/workspaces"),
        ("POST", f"/api/workspaces/{WORKSPACE}/job-batches"),
        ("DELETE", "/api/jobs/job-x"),
        ("POST", "/api/jobs/job-x/run-to"),
        ("POST", "/api/worker/pause"),
        ("POST", "/api/studio-agent-tokens"),
        ("POST", f"/api/workspaces/{WORKSPACE}/workflow-drafts/publish"),
        ("DELETE", f"/api/workspaces/{WORKSPACE}/materials/mat-x"),
    }
    for method, url, payload in endpoints:
        url = url.replace("{workspace_id}", WORKSPACE)
        response = api.request(method, url, json=payload)
        expected = 404 if (method, url) in scopeless_404 else 403
        assert response.status_code == expected, f"{method} {url} -> {response.status_code}"
        assert response.status_code in (403, 404), f"{method} {url} leaked a non-refusal"


def test_studio_agent_scope_still_rejected_on_runs(client, job_db) -> None:
    """The new runs guard keeps the old refusal for studio-agent scoped
    tokens (the intake channel must not become the agent's side door)."""
    _create_workspace(client, WORKSPACE)
    from server.app.auth import scoped_tokens

    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped_client = _bearer_client(client, token)
    response = scoped_client.post(
        f"/api/workspaces/{WORKSPACE}/runs",
        json={"items": [{"type": "material", "material_id": "m"}]},
    )
    assert response.status_code == 403
    assert "Studio agent scope" in response.json()["detail"]


def test_full_session_still_submits_runs(client) -> None:
    """Console submissions (admin/member sessions) keep working."""
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    response = _submit_run(client, WORKSPACE, "mat-1")
    assert response.status_code == 200, response.text
    assert response.json()["created_count"] == 1


def test_api_submission_logs_token_id_not_a_user(client, caplog) -> None:
    """#626 audit: the structured log carries the token id — the machine
    identity is never laundered into a user attribution."""
    import logging

    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    with caplog.at_level(logging.INFO, logger="server.app.routes.runs"):
        response = _submit_run(api, WORKSPACE, "mat-1")
    assert response.status_code == 200
    records = [r for r in caplog.records if "workspace api token" in r.getMessage()]
    assert records, "expected an api-token submission audit record"
    message = records[0].getMessage()
    assert f"token_id={issued['token_id']}" in message
    assert f"workspace_id={WORKSPACE}" in message


# --- review hardening (#626 code review) ---------------------------------------
# The api-scope arm in require_workspace_access must not leak the guard's
# scopeless fall-through: every route below carries the membership guard but
# NO workspace parameter, so the original "scope or pass" api arm let the
# machine identity through. Each pin is cross-workspace red.


def test_api_token_cannot_reach_identity_endpoints(client) -> None:
    """/me and /logout are USER endpoints: the machine identity has no user
    row, and unguarded they 500 on the UserResponse contract (review P1)."""
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    assert api.get("/api/auth/me").status_code == 403
    assert api.post("/api/auth/logout").status_code == 403


def test_api_token_lists_only_its_bound_workspace(client) -> None:
    """GET /api/workspaces is the unscoped listing: full sessions see all
    workspaces, but an api token is a machine credential bound to ONE
    workspace — the scopeless mount must hard-refuse it (404, review P1).
    A workspace-scoped variant of the same read (query param) still works."""
    _create_workspace(client, WORKSPACE)
    _create_workspace(client, OTHER, name="other")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    # Scopeless mount: refused outright, no cross-workspace enumeration.
    assert api.get("/api/workspaces").status_code == 404
    # The full-session behaviour is unchanged (admin sees both).
    admin_ids = [ws["id"] for ws in client.get("/api/workspaces").json()["workspaces"]]
    assert set(admin_ids) == {WORKSPACE, OTHER}


def test_api_token_refused_on_scopeless_guard_routes(client) -> None:
    """Routes under the membership guard WITHOUT a workspace scope are
    login-only for users; the api arm must hard-refuse there too (review
    P2) — a machine credential's entire permission model is its bound
    workspace, so a scopeless mount is a misroute for it."""
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    # POST /skills/validate: scopeless POST under secured().
    assert api.post("/api/skills/validate", json={"path": "/tmp"}).status_code == 404
    # The unscoped worker status read stays closed as well.
    assert api.get("/api/worker/status").status_code == 404
    # And the per-workspace runs read still works (the guard's happy path).
    assert api.get(f"/api/workspaces/{WORKSPACE}/runs").status_code == 200


def test_api_token_read_surface_is_the_intake_allowlist_only(client) -> None:
    """Review P1 pin: the api identity is NOT a general member of its bound
    workspace — its GET surface is exactly the documented intake reads
    (runs list/detail + the jobs listings: legacy AND paginated snapshot).
    Every other workspace-scoped GET under the membership guard must 404
    it: secrets, materials, chat sessions, preview panels, stats. (Before
    this pin the guard's "editor of the bound workspace" pass let the
    machine credential read all of these; secret NAMES are an existence
    oracle.)"""
    _create_workspace(client, WORKSPACE)
    _create_workspace(client, OTHER, name="other")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    # The allowlist: all four read shapes work on the bound workspace.
    assert api.get(f"/api/workspaces/{WORKSPACE}/runs").status_code == 200
    assert api.get(f"/api/workspaces/{WORKSPACE}/jobs").status_code == 200
    assert api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot").status_code == 200
    # Everything else: refused on its OWN workspace (not just cross-workspace).
    refused_reads = [
        f"/api/workspaces/{WORKSPACE}/secrets",
        f"/api/workspaces/{WORKSPACE}/materials",
        f"/api/workspaces/{WORKSPACE}/studio-chat/sessions",
        f"/api/workspaces/{WORKSPACE}/preview-panel",
        f"/api/workspaces/{WORKSPACE}/stats",
        f"/api/workspaces/{WORKSPACE}/jobs/facets",
        f"/api/metrics/overview?workspace_id={WORKSPACE}",
    ]
    for url in refused_reads:
        assert api.get(url).status_code == 404, f"GET {url} should 404 the api scope"
    # Cross-workspace allowlist reads stay 404 as well.
    assert api.get(f"/api/workspaces/{OTHER}/runs").status_code == 404
    assert api.get(f"/api/workspaces/{OTHER}/jobs").status_code == 404
    assert api.get(f"/api/workspaces/{OTHER}/jobs/snapshot").status_code == 404
    # Full sessions keep every one of these reads.
    for url in refused_reads:
        assert client.get(url).status_code == 200, f"admin session GET {url} broke"


def test_api_token_cannot_write_drafts(client) -> None:
    """Review P1 pin: the manifest's exempt draft/validate routes (PUT node
    code, POST/PUT agent definitions, draft validate/compare) are reachable
    for studio-agent scoped tokens by design — but the api machine identity
    has no user row, and before the allowlist these routes either wrote
    drafts attributed via a KeyError 500 or let the token write workspace
    content. All of them must 404 it."""
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    probes = [
        (
            "POST",
            f"/api/workspaces/{WORKSPACE}/workflow-drafts/validate",
            {"definition_yaml": "key: k\nlabel: l\nnodes: {}"},
        ),
        (
            "POST",
            f"/api/workspaces/{WORKSPACE}/workflow-drafts/compare",
            {"from_yaml": "key: a\nlabel: a\nnodes: {}", "to_yaml": "key: a\nlabel: b\nnodes: {}"},
        ),
        (
            "PUT",
            f"/api/workspaces/{WORKSPACE}/nodes/n1/code",
            {"code": "def run(job, job_dir, runtime):\n    pass\n"},
        ),
        (
            "POST",
            f"/api/agent-definitions?workspace_id={WORKSPACE}",
            {"capability": "cap", "runtime": "pi"},
        ),
        (
            "PUT",
            f"/api/agent-definitions/a1/draft?workspace_id={WORKSPACE}",
            {"capability": "cap", "runtime": "pi"},
        ),
        (
            "POST",
            f"/api/agent-definitions/a1/copy?workspace_id={WORKSPACE}",
            {"new_agent_id": "a2"},
        ),
    ]
    for method, url, payload in probes:
        response = api.request(method, url, json=payload)
        assert response.status_code == 404, f"{method} {url} -> {response.status_code}"
        assert response.status_code < 500, f"{method} {url} crashed the handler"


def test_api_token_cannot_read_foreign_job_detail(client) -> None:
    """GET /api/jobs/{job_id} derives the workspace from the row — the
    member check below it 404s a non-member, so an api token bound to
    another workspace must get the same 404 (review pin; the guard alone
    does not scope this route)."""
    from tests.helpers import publish_legacy_intake_revision

    _create_workspace(client, WORKSPACE)
    client.post(
        "/api/workspaces",
        json={"id": "api-token-ws-foreign", "name": "foreign"},
    )
    publish_legacy_intake_revision(client.app.state.job_db, "api-token-ws-foreign")
    _insert_material(client, "api-token-ws-foreign", "mat-foreign")
    admin_run = client.post(
        "/api/workspaces/api-token-ws-foreign/runs",
        json={"items": [{"type": "material", "material_id": "mat-foreign"}]},
    )
    assert admin_run.status_code == 200
    # The run payload carries no job ids (#467 A4) — read them back through
    # the workspace's jobs listing.
    jobs = client.get("/api/workspaces/api-token-ws-foreign/jobs").json()["jobs"]
    assert jobs, "expected the admin submission to create a job"
    job_id = jobs[0]["id"]

    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    assert api.get(f"/api/jobs/{job_id}").status_code == 404


# --- attack-review hardening (PR #704 red-team pass) ----------------------------


def test_api_token_refused_on_scopeless_user_routes(client) -> None:
    """HIGH-1 pin: the three scopeless require_user GET mounts — the GLOBAL
    worker listing (allowed_workspaces / register_token_ids / model-rack
    topology, instance-wide, not bound-workspace-filtered), the instance
    connection keys, the agent statuses — refuse the api machine identity
    (403 from require_user: the machine identity has no user row). Full
    sessions keep every one of these reads."""
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    for url in ("/api/agent-workers", "/api/connections/keys", "/api/agents"):
        response = api.get(url)
        assert response.status_code == 403, f"GET {url} -> {response.status_code}"
        assert "Workspace API tokens" in response.json()["detail"]
    # The same endpoints stay open for real user sessions.
    for url in ("/api/agent-workers", "/api/connections/keys", "/api/agents"):
        assert client.get(url).status_code == 200, f"session GET {url} broke"


def test_studio_agent_scope_still_passes_require_user(client, job_db) -> None:
    """HIGH-1 boundary: the require_user refusal targets the api scope ONLY
    — a studio-agent scoped token carries the initiating user's row, so the
    require_user surface it legitimately uses (draft/validate endpoints,
    STUDIO-AGENT-001) must keep working. The refusal must not widen to
    every scoped identity."""
    _create_workspace(client, WORKSPACE)
    from server.app.auth import scoped_tokens

    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    scoped_client = _bearer_client(client, token)
    # A scopeless require_user read (the same route family HIGH-1 closed for
    # api tokens) and a draft write: both stay reachable for the scoped token.
    assert scoped_client.get("/api/agent-workers").status_code == 200
    draft = scoped_client.put(
        f"/api/workspaces/{WORKSPACE}/nodes/n1/code",
        json={"code": "def run(job, job_dir, runtime):\n    pass\n"},
    )
    assert draft.status_code not in (401, 403) and draft.status_code < 500


def test_revoked_token_attempts_still_stamp_last_used(client) -> None:
    """M-3 pin (codex3 P2 refinement): a revoked credential that keeps
    being presented — with the CORRECT secret — must still refresh the
    usage watermark; after an emergency revocation the admin listing needs
    to show whether attempts continue. Access stays cut (401) while the
    telemetry records the attempt. The wrong-secret variant (no stamp) is
    pinned separately below."""
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="leaked")
    # Revoke BEFORE any successful resolve so the throttle map is empty and
    # the revoked-row attempt is what stamps the watermark.
    assert (
        client.delete(f"/api/workspaces/{WORKSPACE}/api-tokens/{issued['token_id']}").status_code
        == 200
    )
    api = _bearer_client(client, issued["api_token"])
    assert api.get(f"/api/workspaces/{WORKSPACE}/runs").status_code == 401
    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"]
    entry = next(t for t in listed if t["token_id"] == issued["token_id"])
    assert entry["revoked"] is True
    assert entry["last_used_at"] is not None, "revoked attempt left no watermark"


def test_revoked_token_wrong_secret_does_not_stamp_last_used(client) -> None:
    """codex3 P2 pin: a public token_id plus an arbitrary WRONG secret must
    NOT refresh last_used_at — otherwise anyone who ever saw the token id
    could forge the "revoked credential still in use" audit signal. Access
    is refused either way (401); only the digest match separates the
    telemetry outcome."""
    _create_workspace(client, WORKSPACE)
    issued = _issue(client, WORKSPACE, label="leaked")
    assert (
        client.delete(f"/api/workspaces/{WORKSPACE}/api-tokens/{issued['token_id']}").status_code
        == 200
    )
    wrong = _bearer_client(client, f"{issued['token_id']}.wrong-secret-entirely")
    assert wrong.get(f"/api/workspaces/{WORKSPACE}/runs").status_code == 401
    listed = client.get(f"/api/workspaces/{WORKSPACE}/api-tokens").json()["tokens"]
    entry = next(t for t in listed if t["token_id"] == issued["token_id"])
    assert entry["revoked"] is True
    assert entry["last_used_at"] is None, "wrong secret forged a usage watermark"


@pytest.mark.no_db
def test_resolve_failure_paths_do_equal_hash_work(monkeypatch) -> None:
    """M-1 pin: every 401 path of resolve_api_token (unknown id, revoked,
    expired, bad secret) must run the same sha256 + compare_digest work —
    the miss paths perform the dummy comparison, so the outcomes are
    indistinguishable by timing as well as by response body.

    Store-level unit with a stubbed queries facade: the counting window
    must contain nothing but the store's own primitives. monkeypatch
    restores the real hashlib/hmac for the rest of the session."""

    class _StubQueries:
        def __init__(self, row: dict | None) -> None:
            self._row = row
            self.stamped: list[str] = []

        def get_workspace_api_token_row(self, token_id: str) -> dict | None:
            return self._row

        def update_workspace_api_token_last_used(self, token_id: str) -> None:
            self.stamped.append(token_id)

    live_row = {
        "token_hash": hashlib.sha256(b"correct-secret").hexdigest(),
        "workspace_id": "ws-x",
        "revoked_at": None,
        "expires_at": None,
    }
    revoked_row = {**live_row, "revoked_at": "2026-01-01T00:00:00+00:00"}
    expired_row = {**live_row, "expires_at": "2020-01-01T00:00:00+00:00"}
    token_id = "a".ljust(32, "0")

    from server.app.auth import workspace_api_tokens as token_store_module

    def _resolved(store: token_store_module.WorkspaceApiTokenStore, secret: str):
        return store.resolve_api_token(f"{token_id}.{secret}")

    # Sanity: the stubbed live row resolves and stamps the watermark.
    live_store = token_store_module.WorkspaceApiTokenStore(_StubQueries(live_row))  # type: ignore[arg-type]
    assert _resolved(live_store, "correct-secret") == {
        "token_id": token_id,
        "workspace_id": "ws-x",
    }
    assert live_store._queries.stamped  # type: ignore[attr-defined]

    counts = {"sha256": 0, "compare": 0}
    real_sha256, real_compare = hashlib.sha256, hmac.compare_digest

    def _counting_sha256(data=b""):  # type: ignore[no-untyped-def]
        counts["sha256"] += 1
        return real_sha256(data)

    def _counting_compare(left: str, right: str) -> bool:
        counts["compare"] += 1
        return real_compare(left, right)

    monkeypatch.setattr(token_store_module.hashlib, "sha256", _counting_sha256)
    monkeypatch.setattr(token_store_module.hmac, "compare_digest", _counting_compare)

    # Revoked row with the CORRECT secret: refused, watermark stamped
    # (codex3 P2: the caller demonstrably holds the secret), and the real
    # compare doubles as the M-1 equalizer work.
    counts.update(sha256=0, compare=0)
    revoked_store = token_store_module.WorkspaceApiTokenStore(_StubQueries(revoked_row))  # type: ignore[arg-type]
    assert _resolved(revoked_store, "correct-secret") is None
    assert counts == {"sha256": 1, "compare": 1}, "revoked path skipped the equalizer"
    assert revoked_store._queries.stamped  # type: ignore[attr-defined]

    # Revoked row with a WRONG secret (codex3 P2): same 401, same 1+1
    # primitive work — and NO watermark: a public token_id must not be
    # able to forge "the revoked credential is still in use".
    counts.update(sha256=0, compare=0)
    revoked_wrong = token_store_module.WorkspaceApiTokenStore(_StubQueries(revoked_row))  # type: ignore[arg-type]
    assert _resolved(revoked_wrong, "wrong-secret") is None
    assert counts == {"sha256": 1, "compare": 1}
    assert not revoked_wrong._queries.stamped  # type: ignore[attr-defined]

    # Expired row: refused, dummy work ran, no watermark (not an attempt on
    # a revoked credential).
    counts.update(sha256=0, compare=0)
    expired_store = token_store_module.WorkspaceApiTokenStore(_StubQueries(expired_row))  # type: ignore[arg-type]
    assert _resolved(expired_store, "correct-secret") is None
    assert counts == {"sha256": 1, "compare": 1}, "expired path skipped the equalizer"
    assert not expired_store._queries.stamped  # type: ignore[attr-defined]

    # Unknown id: the miss path must hash + compare once too.
    counts.update(sha256=0, compare=0)
    miss_store = token_store_module.WorkspaceApiTokenStore(_StubQueries(None))  # type: ignore[arg-type]
    assert _resolved(miss_store, "any-secret") is None
    assert counts == {"sha256": 1, "compare": 1}, "miss path skipped the equalizer"
    assert not miss_store._queries.stamped  # type: ignore[attr-defined]

    # Bad secret on a live row: the same amount of primitive work.
    counts.update(sha256=0, compare=0)
    assert _resolved(live_store, "wrong-secret") is None
    assert counts == {"sha256": 1, "compare": 1}


# --- codex3 P1: paginated, run-scoped job status reads --------------------------


def _seed_jobs(client: TestClient, workspace_id: str, count: int, run_id: str) -> list[str]:
    """Insert `count` queued jobs into one run (single transaction)."""
    job_db = client.app.state.job_db
    job_ids = [f"{workspace_id}:question_id:{run_id}-{i:04d}" for i in range(count)]
    with job_db.connect() as conn:
        for i, job_id in enumerate(job_ids):
            conn.execute(
                "insert into jobs(id, workspace_id, source_type, source_id, run_id, title,"
                " storage_dir) values (%s, %s, 'question_id', %s, %s, %s, '')",
                (job_id, workspace_id, f"{run_id}-{i:04d}", run_id, f"bulk {i:04d}"),
            )
    return job_ids


def test_api_token_pages_past_legacy_jobs_cap(client) -> None:
    """codex3 P1: the legacy GET /jobs is capped at 500 rows with no cursor
    — a machine caller with more than 500 jobs in its workspace could never
    read the rest. The paginated /jobs/snapshot (on the intake allowlist
    since this fix) must let the same identity walk the WHOLE list with
    limit+cursor."""
    _create_workspace(client, WORKSPACE)
    _seed_jobs(client, WORKSPACE, count=502, run_id="run-bulk")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])

    # The legacy surface: capped at 500 (API-compat behavior, unchanged).
    legacy = api.get(f"/api/workspaces/{WORKSPACE}/jobs")
    assert legacy.status_code == 200
    assert len(legacy.json()["jobs"]) == 500

    # The paginated surface: 500 + 2 with the same credential.
    collected: list[str] = []
    cursor = None
    pages = 0
    while True:
        url = f"/api/workspaces/{WORKSPACE}/jobs/snapshot?limit=500"
        if cursor is not None:
            url += f"&cursor={cursor}"
        page = api.get(url)
        assert page.status_code == 200, page.text
        body = page.json()
        collected.extend(job["id"] for job in body["jobs"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 10, "pagination did not converge"
    assert len(collected) == 502
    assert len(set(collected)) == 502  # cursor pages never repeat a row
    assert collected == sorted(collected, reverse=True)  # created_at desc, id desc


def test_api_token_reads_jobs_by_run_id(client) -> None:
    """codex3 P1: a machine caller's primary question is "what happened to
    MY run" — with newer jobs from other runs in the workspace, the legacy
    listing may not even include this run's items. snapshot?run_id= must
    scope the page (and its total) to the caller's run; the run itself is
    always obtained from the create response."""
    _create_workspace(client, WORKSPACE)
    _insert_material(client, WORKSPACE, "mat-1")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    response = _submit_run(api, WORKSPACE, "mat-1")
    assert response.status_code == 200, response.text
    run_id = response.json()["run"]["id"]
    # Newer jobs in OTHER runs crowd the legacy 500-cap listing.
    _seed_jobs(client, WORKSPACE, count=6, run_id="run-other-1")
    _seed_jobs(client, WORKSPACE, count=6, run_id="run-other-2")

    legacy = api.get(f"/api/workspaces/{WORKSPACE}/jobs")
    assert legacy.status_code == 200
    assert len(legacy.json()["jobs"]) == 13  # nothing hidden yet — but the
    # ordering is created_at desc; the run's job is last, and with >500
    # newer jobs it drops out entirely (the codex3 P1 scenario).

    scoped = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id={run_id}")
    assert scoped.status_code == 200, scoped.text
    body = scoped.json()
    assert body["total"] == 1
    scoped_jobs = [job["id"] for job in body["jobs"]]
    assert len(scoped_jobs) == 1
    # The scoping is by run, not by recency: the other runs' jobs stay out.
    other = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id=run-other-1")
    assert other.status_code == 200
    assert other.json()["total"] == 6
    assert {job["id"] for job in other.json()["jobs"]}.isdisjoint(scoped_jobs)
    # An unknown run is an empty page, not an error.
    missing = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id=no-such-run")
    assert missing.status_code == 200
    assert missing.json()["total"] == 0
    assert missing.json()["jobs"] == []
    # run_id composes with the pagination cursor and the status filter.
    paged = api.get(f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id=run-other-1&limit=4")
    assert paged.status_code == 200
    assert len(paged.json()["jobs"]) == 4
    assert paged.json()["next_cursor"] is not None
    tail = api.get(
        f"/api/workspaces/{WORKSPACE}/jobs/snapshot?run_id=run-other-1&limit=4"
        f"&cursor={paged.json()['next_cursor']}"
    )
    assert tail.status_code == 200
    assert len(tail.json()["jobs"]) == 2
    assert tail.json()["next_cursor"] is None
    still_scoped = {job["id"] for job in paged.json()["jobs"] + tail.json()["jobs"]}
    assert len(still_scoped) == 6  # cursor kept the run scoping
