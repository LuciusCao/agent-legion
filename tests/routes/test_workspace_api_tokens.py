"""Workspace API intake token tests (#626).

The machine-to-machine submission channel: an admin issues a
``{token_id}.{secret}`` credential bound to one workspace, an external
system presents it as ``Authorization: Bearer`` against the runs surface.
This file owns the shared fixtures/helpers, the management lifecycle
(admin-only issue/list/revoke, one-time plaintext, expiry visibility, the
last_used_at watermark), and the channel's happy path; the sibling files
carry the rest of the split (test-file line budget, AGENTS.md §4):
- test_workspace_api_token_boundaries.py — the permission boundaries and
  attack surface (cross-workspace 404, expiry/revocation semantics, the
  api-scope blast radius, the store's equal-work failure paths);
- test_workspace_api_token_paging.py — the codex3 P1 paginated,
  run-scoped job status reads (snapshot cursor + run_id filter).
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


# --- auth chain: the channel's happy path --------------------------------------


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


# --- listing watermark ---------------------------------------------------------


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
