"""Runs async mode API (#1117): 202 accept, batch handle polling, control plane.

Service semantics live in tests/services/test_run_service_async.py +
test_run_batch_feeder.py; this file owns the HTTP contract (status codes,
payload shape, auth matrix, the #626 machine-channel loop).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import publish_legacy_intake_revision

WORKSPACE = "runs-async-ws"


def _create_workspace(client: TestClient, ws_id: str = WORKSPACE) -> str:
    response = client.post("/api/workspaces", json={"id": ws_id, "name": ws_id})
    assert response.status_code == 200, response.text
    publish_legacy_intake_revision(client.app.state.job_db, ws_id)
    return ws_id


def _insert_material(client: TestClient, workspace_id: str, material_id: str) -> None:
    job_db = client.app.state.job_db
    with job_db.connect() as conn:
        conn.execute(
            "insert into materials(id, workspace_id, content_hash, filename, content_type,"
            " size_bytes, storage_key, status, created_by)"
            " values (%s, %s, %s, 'doc.txt', 'text/plain', 10, %s, 'ready', 'tester')",
            (material_id, workspace_id, f"hash-{material_id}", f"{workspace_id}/h/doc.txt"),
        )


def _submit_async(client: TestClient, workspace_id: str, items: list[dict]):
    return client.post(
        f"/api/workspaces/{workspace_id}/runs",
        json={"items": items, "mode": "async"},
    )


def test_async_submit_returns_202_handle(client: TestClient) -> None:
    ws = _create_workspace(client)
    _insert_material(client, ws, "mat-1")

    response = _submit_async(client, ws, [{"type": "material", "material_id": "mat-1"}])

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["run"]["status"] == "created"
    assert body["run"]["created_count"] == 0
    batch = body["batch"]
    assert batch["run_id"] == body["run"]["id"]
    assert batch["status"] == "pending"
    assert batch["items_total"] == 1
    assert batch["processed_count"] == 0 and batch["jobs_created"] == 0

    # 轮询面：GET detail 携带 batch 句柄 + job_stats。
    detail = client.get(f"/api/workspaces/{ws}/runs/{body['run']['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["batch"]["status"] == "pending"
    assert detail.json()["job_stats"] == {"total": 0, "by_status": {}}


def test_default_mode_stays_sync_200(client: TestClient) -> None:
    ws = _create_workspace(client)
    _insert_material(client, ws, "mat-1")

    response = client.post(
        f"/api/workspaces/{ws}/runs",
        json={"items": [{"type": "material", "material_id": "mat-1"}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_count"] == 1 and len(body["job_ids"]) == 1
    # 同步 run 的 detail 不带 batch 句柄。
    detail = client.get(f"/api/workspaces/{ws}/runs/{body['run']['id']}").json()
    assert detail["batch"] is None


def test_unknown_mode_is_422(client: TestClient) -> None:
    ws = _create_workspace(client)
    response = client.post(
        f"/api/workspaces/{ws}/runs",
        json={"items": [{"type": "material", "material_id": "m"}], "mode": "later"},
    )
    assert response.status_code == 422


def test_async_blind_retry_returns_the_same_handle(client: TestClient) -> None:
    ws = _create_workspace(client)
    _insert_material(client, ws, "mat-1")
    items = [{"type": "material", "material_id": "mat-1"}]

    first = _submit_async(client, ws, items).json()
    second = _submit_async(client, ws, items)

    assert second.status_code == 202
    assert second.json()["run"]["id"] == first["run"]["id"]
    assert second.json()["batch"]["status"] == "pending"


def test_pause_resume_cancel_cycle(client: TestClient) -> None:
    ws = _create_workspace(client)
    _insert_material(client, ws, "mat-1")
    run_id = _submit_async(client, ws, [{"type": "material", "material_id": "mat-1"}]).json()[
        "run"
    ]["id"]

    paused = client.post(f"/api/workspaces/{ws}/runs/{run_id}/pause")
    assert paused.status_code == 200, paused.text
    assert paused.json()["batch"]["status"] == "paused"
    assert paused.json()["run"]["id"] == run_id
    # CAS miss → 409（不静默成功）。
    assert client.post(f"/api/workspaces/{ws}/runs/{run_id}/pause").status_code == 409

    resumed = client.post(f"/api/workspaces/{ws}/runs/{run_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["batch"]["status"] == "pending"

    cancelled = client.post(f"/api/workspaces/{ws}/runs/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["batch"]["status"] == "cancelled"
    assert cancelled.json()["batch"]["finished_at"] is not None
    assert client.post(f"/api/workspaces/{ws}/runs/{run_id}/cancel").status_code == 409
    assert client.post(f"/api/workspaces/{ws}/runs/{run_id}/resume").status_code == 409


def test_control_plane_404s(client: TestClient) -> None:
    ws = _create_workspace(client)
    _insert_material(client, ws, "mat-1")
    sync_run_id = client.post(
        f"/api/workspaces/{ws}/runs", json={"items": [{"type": "material", "material_id": "mat-1"}]}
    ).json()["run"]["id"]

    # 同步 run 无 batch 句柄；未知 run id 同形 404（防枚举）。
    assert client.post(f"/api/workspaces/{ws}/runs/{sync_run_id}/pause").status_code == 404
    assert client.post(f"/api/workspaces/{ws}/runs/no-such-run/cancel").status_code == 404


def test_control_plane_requires_auth(anon_client: TestClient) -> None:
    assert anon_client.post("/api/workspaces/ws-1/runs/run-1/pause").status_code == 401
    assert anon_client.post("/api/workspaces/ws-1/runs/run-1/cancel").status_code == 401
    response = anon_client.post(
        "/api/workspaces/ws-1/runs",
        json={"items": [{"type": "material", "material_id": "m"}], "mode": "async"},
    )
    assert response.status_code == 401


def test_api_token_runs_the_whole_async_loop(client: TestClient) -> None:
    """#626 机器通道闭环：Bearer token 提交(async) → 轮询 → 取消。"""
    ws = _create_workspace(client)
    _insert_material(client, ws, "mat-1")
    issued = client.post(f"/api/workspaces/{ws}/api-tokens", json={"label": "ci"})
    assert issued.status_code == 201, issued.text
    api_client = client.__class__(client.app)
    api_client.headers["authorization"] = f"Bearer {issued.json()['api_token']}"

    accepted = _submit_async(api_client, ws, [{"type": "material", "material_id": "mat-1"}])
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run"]["id"]

    detail = api_client.get(f"/api/workspaces/{ws}/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["batch"]["status"] == "pending"

    cancelled = api_client.post(f"/api/workspaces/{ws}/runs/{run_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["batch"]["status"] == "cancelled"

    # 绑定他 workspace 的 token 对本 workspace 404（守卫同 create_run）。
    other = _create_workspace(client, "runs-async-ws-other")
    other_issued = client.post(f"/api/workspaces/{other}/api-tokens", json={"label": "ci"})
    outsider = client.__class__(client.app)
    outsider.headers["authorization"] = f"Bearer {other_issued.json()['api_token']}"
    assert outsider.post(f"/api/workspaces/{ws}/runs/{run_id}/cancel").status_code == 404


def test_control_plane_admits_session_admin(client: TestClient) -> None:
    """守卫挂载钉住：session 成员过 require_workspace_api_intake（service 404
    而非 auth 403/401）；scoped 身份的细分拒绝矩阵由 #626 的
    test_workspace_api_token_boundaries.py 覆盖（同一守卫）。"""
    ws = _create_workspace(client)
    assert client.post(f"/api/workspaces/{ws}/runs/any-run/cancel").status_code == 404
