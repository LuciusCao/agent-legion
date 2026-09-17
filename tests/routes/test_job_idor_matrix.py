"""Cross-workspace access matrix for job-id-shaped routes (#710).

``require_workspace_access`` historically only checked membership when the
route carried a ``workspace_id``; the 13 bare ``/jobs/{job_id}`` endpoints
(read detail/artifacts/logs/token-usage, delete, rerun/run-to/continue,
upgrade-workflow, and the invalid-subpath catch-all) therefore let any
logged-in user read, mutate, or delete another workspace's jobs. These
tests pin the fixed behavior: the job's own workspace is the authorization
scope, unknown jobs 404 (enumeration-safe), and a workspace prefix that
does not match the job's actual workspace is rejected even for members.
"""

from __future__ import annotations

import pytest

from tests.helpers import publish_legacy_intake_revision, seed_workspace_agent_definitions

CSRF = {"x-agent-legion-request": "1"}


def _create_member(client, username: str, password: str) -> str:
    response = client.post(
        "/api/users",
        json={"username": username, "password": password},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _member_client(client, username: str, password: str):
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _make_job(client, workspace_id: str) -> str:
    seed_workspace_agent_definitions(workspace_id)
    publish_legacy_intake_revision(client.app.state.job_db, workspace_id)
    created = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": workspace_id,
            "source_kind": "direct_ids",
            "knowledge_point_ids": ["Q001"],
        },
        headers=CSRF,
    )
    assert created.status_code == 200, created.text
    return created.json()["jobs"][0]["id"]


@pytest.fixture
def two_workspaces_with_jobs(client) -> tuple[str, str, str, str]:
    """(workspace A id, workspace B id, job in A, job in B)."""
    ws_a = client.post(
        "/api/workspaces", json={"id": "ws_alpha", "name": "Alpha"}, headers=CSRF
    ).json()["workspace"]["id"]
    ws_b = client.post(
        "/api/workspaces", json={"id": "ws_beta", "name": "Beta"}, headers=CSRF
    ).json()["workspace"]["id"]
    return ws_a, ws_b, _make_job(client, ws_a), _make_job(client, ws_b)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/jobs/{job_id}"),
        ("GET", "/api/jobs/{job_id}/artifacts/some-artifact"),
        ("GET", "/api/jobs/{job_id}/artifacts/some-artifact/raw"),
        ("GET", "/api/jobs/{job_id}/runs/1/log"),
        ("GET", "/api/jobs/{job_id}/token-usage"),
        ("GET", "/api/jobs/{job_id}/runs/1/token-usage"),
        ("DELETE", "/api/jobs/{job_id}"),
        ("POST", "/api/jobs/{job_id}/nodes/node-1/rerun"),
        ("POST", "/api/jobs/{job_id}/run-to"),
        ("POST", "/api/jobs/{job_id}/continue"),
        ("POST", "/api/jobs/{job_id}/upgrade-workflow"),
        ("GET", "/api/jobs/{job_id}/invalid/subpath"),
    ],
)
def test_non_member_cannot_touch_other_workspace_job(
    client, two_workspaces_with_jobs, method, path
) -> None:
    """A logged-in non-member of workspace A gets 404 on every job-id route
    pointing at A's job — regardless of their role anywhere else."""
    ws_a, ws_b, job_a, _job_b = two_workspaces_with_jobs
    _create_member(client, "outsider", "pw-outsider")
    outsider = _member_client(client, "outsider", "pw-outsider")
    # Sanity: the outsider IS a functioning account (its own view works).
    assert outsider.get("/api/auth/me").status_code == 200

    request = getattr(outsider, method.lower())
    response = request(path.format(job_id=job_a))
    assert response.status_code == 404, (method, path, response.text)
    # The job must survive the DELETE attempt.
    if method == "DELETE":
        assert client.get(f"/api/jobs/{job_a}", headers=CSRF).status_code == 200
    _ = ws_b


def test_other_workspace_viewer_cannot_read_or_delete(client, two_workspaces_with_jobs) -> None:
    """Membership in workspace B grants nothing on workspace A's jobs —
    the IDOR's exact shape from the review (#710)."""
    ws_a, ws_b, job_a, _job_b = two_workspaces_with_jobs
    member_id = _create_member(client, "beta-viewer", "pw-beta")
    job_db = client.app.state.job_db
    job_db.upsert_workspace_member(ws_b, member_id, "viewer")

    beta = _member_client(client, "beta-viewer", "pw-beta")
    assert beta.get(f"/api/jobs/{job_a}").status_code == 404
    assert beta.get(f"/api/jobs/{job_a}/token-usage").status_code == 404
    assert beta.delete(f"/api/jobs/{job_a}").status_code == 404
    assert beta.post(f"/api/jobs/{job_a}/run-to").status_code == 404
    assert client.get(f"/api/jobs/{job_a}", headers=CSRF).status_code == 200


def test_member_roles_still_work_on_own_workspace(client, two_workspaces_with_jobs) -> None:
    """No regression: the guard change must not lock members out of their
    own workspace's job routes."""
    ws_a, _ws_b, job_a, _job_b = two_workspaces_with_jobs
    member_id = _create_member(client, "alpha-viewer", "pw-alpha")
    client.app.state.job_db.upsert_workspace_member(ws_a, member_id, "viewer")

    viewer = _member_client(client, "alpha-viewer", "pw-alpha")
    assert viewer.get(f"/api/jobs/{job_a}").status_code == 200
    assert viewer.get(f"/api/jobs/{job_a}/token-usage").status_code == 200
    # Viewer of the owning workspace still cannot mutate (403, not 404:
    # membership is visible, the role is not).
    assert viewer.delete(f"/api/jobs/{job_a}").status_code == 403
    assert viewer.post(f"/api/jobs/{job_a}/run-to").status_code == 403


def test_workspace_prefix_cannot_borrow_foreign_job_id(client, two_workspaces_with_jobs) -> None:
    """``/workspaces/{own}/jobs/{foreign_job}`` mixed shapes 404 even for
    the workspace's own editor — the path scope and the job's real
    workspace must agree."""
    ws_a, ws_b, job_a, _job_b = two_workspaces_with_jobs
    member_id = _create_member(client, "beta-editor", "pw-beta-e")
    client.app.state.job_db.upsert_workspace_member(ws_b, member_id, "editor")

    beta = _member_client(client, "beta-editor", "pw-beta-e")
    # Beta's own workspace is fully accessible...
    assert beta.get(f"/api/workspaces/{ws_b}").status_code == 200
    # ...but cannot borrow Alpha's job under its own prefix.
    assert beta.get(f"/api/workspaces/{ws_b}/jobs/{job_a}/approvals").status_code == 404
    assert (
        beta.post(
            f"/api/workspaces/{ws_b}/jobs/{job_a}/nodes/node-1/approval",
            json={"decision": "approved"},
        ).status_code
        == 404
    )


def test_admin_still_passes_and_unknown_job_404s(client, two_workspaces_with_jobs) -> None:
    ws_a, _ws_b, job_a, _job_b = two_workspaces_with_jobs
    # Admin keeps cross-workspace access by design.
    assert client.get(f"/api/jobs/{job_a}", headers=CSRF).status_code == 200
    # Unknown job ids 404 for logged-in non-admins (enumeration-safe)...
    _create_member(client, "enum-probe", "pw-enum")
    probe = _member_client(client, "enum-probe", "pw-enum")
    assert probe.get("/api/jobs/does_not_exist_job").status_code == 404
    assert probe.get("/api/jobs/does_not_exist_job/token-usage").status_code == 404
