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

from datetime import UTC, datetime, timedelta

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
    (runs list/detail + jobs listing). Every other workspace-scoped GET
    under the membership guard must 404 it: secrets, materials, chat
    sessions, preview panels, stats. (Before this pin the guard's
    "editor of the bound workspace" pass let the machine credential read
    all of these; secret NAMES are an existence oracle.)"""
    _create_workspace(client, WORKSPACE)
    _create_workspace(client, OTHER, name="other")
    issued = _issue(client, WORKSPACE, label="cms")
    api = _bearer_client(client, issued["api_token"])
    # The allowlist: all three read shapes work on the bound workspace.
    assert api.get(f"/api/workspaces/{WORKSPACE}/runs").status_code == 200
    assert api.get(f"/api/workspaces/{WORKSPACE}/jobs").status_code == 200
    # Everything else: refused on its OWN workspace (not just cross-workspace).
    refused_reads = [
        f"/api/workspaces/{WORKSPACE}/secrets",
        f"/api/workspaces/{WORKSPACE}/materials",
        f"/api/workspaces/{WORKSPACE}/studio-chat/sessions",
        f"/api/workspaces/{WORKSPACE}/preview-panel",
        f"/api/workspaces/{WORKSPACE}/stats",
        f"/api/workspaces/{WORKSPACE}/jobs/snapshot",
        f"/api/metrics/overview?workspace_id={WORKSPACE}",
    ]
    for url in refused_reads:
        assert api.get(url).status_code == 404, f"GET {url} should 404 the api scope"
    # Cross-workspace allowlist reads stay 404 as well.
    assert api.get(f"/api/workspaces/{OTHER}/runs").status_code == 404
    assert api.get(f"/api/workspaces/{OTHER}/jobs").status_code == 404
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
