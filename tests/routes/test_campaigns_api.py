"""Campaigns API contract tests (#532 PR-A).

Pins the endpoint surface (create JSON / list / detail / pause / resume /
cancel), the permission matrix (viewer 403 on writes but 200 on reads,
non-member 404 — anti-enumeration, studio-agent token refused on every
route of the group), and the PR-A intermediate state: a created campaign
stays ``pending`` (no feeder in this slice) and is fully visible over the
API. The submit manifest channels (upload/inline/RunItem contract/size
gates) and the preview endpoints live in the sibling file
tests/routes/test_campaigns_api_upload_preview.py (PR #541 round-3 P1
split, zero-churn migration).
"""

from __future__ import annotations

import pytest

CSRF = {"x-agent-legion-request": "1"}

_NODE_KEYS = [
    "intake_knowledge_points",
    "write_script",
    "review_script",
    "publish_content",
]

_CREATE_COUNT = 0


def _create_workspace(client, job_db) -> str:
    global _CREATE_COUNT
    _CREATE_COUNT += 1
    ws_id = "campaign_api_ws" if _CREATE_COUNT == 1 else f"campaign_api_ws_{_CREATE_COUNT}"
    response = client.post("/api/workspaces", json={"id": ws_id, "name": "Campaign WS"})
    assert response.status_code == 200, response.text
    from tests.helpers import publish_builtin_revision

    publish_builtin_revision(job_db, ws_id)
    return ws_id


@pytest.fixture(autouse=True)
def _reset_create_count():
    global _CREATE_COUNT
    _CREATE_COUNT = 0
    yield


def _seed_failed_jobs(client, job_db, workspace_id: str, count: int) -> list[str]:
    batch = job_db.create_run(
        workspace_id,
        "batch_by_ids",
        {"question_ids": [f"Q{i}" for i in range(count)]},
        workspace_id=workspace_id,
    )
    ids: list[str] = []
    for i in range(count):
        job = job_db.create_job(
            workflow_key=workspace_id,
            source_type="question",
            source_id=f"Q{i}",
            run_id=batch["id"],
            title=f"Q{i}",
            node_keys=_NODE_KEYS,
            workspace_id=workspace_id,
        )
        job_db.update_job_status(job["id"], "failed", "boom")
        ids.append(str(job["id"]))
    return ids


def _create_member(client, username="campaign-member", password="pw1") -> str:
    response = client.post(
        "/api/users", json={"username": username, "password": password}, headers=CSRF
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _member_client(client, username="campaign-member", password="pw1"):
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _rerun_body(ids: list[str], **knobs) -> dict:
    body = {"mode": "rerun", "rerun": {"job_ids": ids, "node_key": _NODE_KEYS[0]}}
    body["rerun"].update(knobs)
    return body


# ---------------------------------------------------------------------------
# Contract: create / list / detail
# ---------------------------------------------------------------------------


def test_campaigns_require_auth(anon_client) -> None:
    url = "/api/workspaces/ws-1/campaigns"
    assert anon_client.get(url).status_code == 401
    assert anon_client.post(url, json={}).status_code == 401


def test_create_rerun_campaign_stays_pending(client, job_db) -> None:
    """PR-A 中间态：无 feeder，创建的 campaign 停在 pending、API 可见。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 3)
    response = client.post(f"/api/workspaces/{workspace_id}/campaigns", json=_rerun_body(ids))
    assert response.status_code == 200, response.text
    body = response.json()["campaign"]
    assert body["mode"] == "rerun"
    assert body["status"] == "pending"
    assert body["watermark"] == 30_000
    assert body["batch_size"] == 5_000
    assert body["target_spec"]["job_ids"] == sorted(ids)
    assert body["created_by"] != ""  # the session user id flows through

    listed = client.get(f"/api/workspaces/{workspace_id}/campaigns").json()["campaigns"]
    assert [c["id"] for c in listed] == [body["id"]]

    detail = client.get(f"/api/workspaces/{workspace_id}/campaigns/{body['id']}")
    assert detail.status_code == 200
    assert detail.json()["campaign"]["status"] == "pending"


def test_create_with_knob_overrides(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 2)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json=_rerun_body(ids, watermark=100, batch_size=50),
    )
    assert response.status_code == 200, response.text
    campaign = response.json()["campaign"]
    assert campaign["watermark"] == 100
    assert campaign["batch_size"] == 50


def test_create_from_filter_form(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    _seed_failed_jobs(client, job_db, workspace_id, 4)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json={
            "mode": "rerun",
            "rerun": {"filter": {"status": "failed"}, "node_key": _NODE_KEYS[0]},
        },
    )
    assert response.status_code == 200, response.text
    campaign = response.json()["campaign"]
    assert campaign["target_spec"]["filter"]["status"] == "failed"
    # filter 形态不物化 ids 快照（设计 §1.4 的行宽护栏）。
    assert "job_ids" not in campaign["target_spec"]


def test_create_upgrade_mode(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 2)
    response = client.post(
        f"/api/workspaces/{workspace_id}/campaigns",
        json={"mode": "upgrade", "rerun": {"job_ids": ids}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["campaign"]["mode"] == "upgrade"


def test_create_validation_4xx(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    # pydantic: node_key required without from_failed_node
    assert client.post(base, json={"mode": "rerun", "rerun": {"job_ids": ids}}).status_code == 422
    # pydantic: exactly one of job_ids / filter
    assert (
        client.post(
            base,
            json={
                "mode": "rerun",
                "rerun": {
                    "job_ids": ids,
                    "filter": {"status": "failed"},
                    "node_key": _NODE_KEYS[0],
                },
            },
        ).status_code
        == 422
    )
    # service: unknown mode is a 400 via the contract's Literal → 422
    assert client.post(base, json={"mode": "bogus", "rerun": {"job_ids": ids}}).status_code == 422
    # service: empty selection fail-fast
    assert (
        client.post(
            base,
            json={
                "mode": "rerun",
                "rerun": {"filter": {"status": "completed"}, "node_key": _NODE_KEYS[0]},
            },
        ).status_code
        == 400
    )


def test_unknown_campaign_404(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    assert client.get(f"{base}/nope").status_code == 404
    assert client.post(f"{base}/nope/pause").status_code == 404
    assert client.post(f"{base}/nope/resume").status_code == 404
    assert client.post(f"{base}/nope/cancel").status_code == 404


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


def test_pause_resume_cancel_cycle(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    campaign_id = client.post(base, json=_rerun_body(ids)).json()["campaign"]["id"]

    paused = client.post(f"{base}/{campaign_id}/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["campaign"]["status"] == "paused"

    resumed = client.post(f"{base}/{campaign_id}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["campaign"]["status"] == "running"

    # Double resume is a 409.
    assert client.post(f"{base}/{campaign_id}/resume").status_code == 409

    cancelled = client.post(f"{base}/{campaign_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["campaign"]["status"] == "cancelled"

    # Terminal: every further transition is a 409.
    assert client.post(f"{base}/{campaign_id}/cancel").status_code == 409
    assert client.post(f"{base}/{campaign_id}/pause").status_code == 409
    assert client.post(f"{base}/{campaign_id}/resume").status_code == 409


# ---------------------------------------------------------------------------
# Permission matrix
# ---------------------------------------------------------------------------


def test_viewer_reads_but_cannot_write(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 2)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    created = client.post(base, json=_rerun_body(ids))
    assert created.status_code == 200
    campaign_id = created.json()["campaign"]["id"]

    member_id = _create_member(client)
    job_db.upsert_workspace_member(workspace_id, member_id, "viewer")
    viewer = _member_client(client)

    # Reads pass (SAFE methods carry viewer read rights).
    assert viewer.get(base).status_code == 200
    assert viewer.get(f"{base}/{campaign_id}").status_code == 200

    # Preview is a POST (non-SAFE) and carries the editor gate like every
    # write-shaped route; it writes nothing, but the access layer's SAFE/
    # non-SAFE split is the uniform rule.
    assert (
        viewer.post(
            base + "/preview",
            json={"mode": "rerun", "rerun": {"job_ids": ids, "node_key": _NODE_KEYS[0]}},
        ).status_code
        == 403
    )

    # Every write is 403.
    assert viewer.post(base, json=_rerun_body(ids)).status_code == 403
    assert viewer.post(f"{base}/{campaign_id}/pause").status_code == 403
    assert viewer.post(f"{base}/{campaign_id}/resume").status_code == 403
    assert viewer.post(f"{base}/{campaign_id}/cancel").status_code == 403
    upload = viewer.post(
        f"{base}/upload",
        files={"manifest": ("m.jsonl", b"{}", "text/plain")},
        data={"mode": "submit"},
    )
    assert upload.status_code == 403


def test_non_member_gets_404(client, job_db) -> None:
    """防枚举：非成员读也 404（不是 403）。"""
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    campaign_id = client.post(base, json=_rerun_body(ids)).json()["campaign"]["id"]

    _create_member(client)
    member = _member_client(client)
    assert member.get(base).status_code == 404
    assert member.get(f"{base}/{campaign_id}").status_code == 404
    assert member.post(base, json=_rerun_body(ids)).status_code == 404
    assert member.post(f"{base}/{campaign_id}/pause").status_code == 404


def test_editor_can_write(client, job_db) -> None:
    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    member_id = _create_member(client, username="campaign-editor")
    job_db.upsert_workspace_member(workspace_id, member_id, "editor")
    editor = _member_client(client, username="campaign-editor")

    created = editor.post(base, json=_rerun_body(ids))
    assert created.status_code == 200, created.text
    campaign_id = created.json()["campaign"]["id"]
    assert editor.post(f"{base}/{campaign_id}/pause").status_code == 200


def test_studio_agent_scope_refused_on_every_route(client, job_db) -> None:
    """STUDIO-AGENT-001：campaign 全部路由拒 studio-agent token（写面尤甚）。"""
    from server.app.auth import scoped_tokens

    workspace_id = _create_workspace(client, job_db)
    ids = _seed_failed_jobs(client, job_db, workspace_id, 1)
    base = f"/api/workspaces/{workspace_id}/campaigns"
    created = client.post(base, json=_rerun_body(ids))
    assert created.status_code == 200
    campaign_id = created.json()["campaign"]["id"]

    admin_id = str(job_db.get_user_credentials("admin")["id"])
    token = scoped_tokens.mint_scoped_token(
        job_db, admin_id, scope="studio_agent", workspace_id=workspace_id
    )
    agent = client.__class__(client.app)
    agent.headers["authorization"] = f"Bearer {token}"

    def _call(method: str, path: str, **kwargs):
        response = getattr(agent, method)(path, **kwargs)
        return response.status_code

    assert _call("get", base) == 403
    assert _call("get", f"{base}/{campaign_id}") == 403
    assert _call("post", base, json=_rerun_body(ids)) == 403
    assert _call("post", f"{base}/{campaign_id}/pause") == 403
    assert _call("post", f"{base}/{campaign_id}/resume") == 403
    assert _call("post", f"{base}/{campaign_id}/cancel") == 403
