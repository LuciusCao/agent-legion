"""Run-batches task center API (#1157 phase3): views, keyset paging, archive.

Service/query semantics live in tests/services/test_run_batch_listing.py;
this file owns the HTTP contract (status codes, payload shape, the archive
409/404 matrix, auth).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from tests.helpers import publish_legacy_intake_revision

WORKSPACE = "run-batches-ws"


def _create_workspace(client: TestClient, ws_id: str = WORKSPACE) -> str:
    response = client.post("/api/workspaces", json={"id": ws_id, "name": ws_id})
    assert response.status_code == 200, response.text
    publish_legacy_intake_revision(client.app.state.job_db, ws_id)
    return ws_id


def _insert_batch(
    client: TestClient,
    workspace_id: str,
    run_id: str,
    *,
    status: str,
    finished_at: datetime | None = None,
    archived_at: datetime | None = None,
    items_total: int = 3,
    jobs_created: int = 0,
    created_at: datetime | None = None,
) -> None:
    job_db = client.app.state.job_db
    with job_db.connect() as conn:
        conn.execute(
            "insert into runs(id, workspace_id, source_kind, status, created_at)"
            " values (%s, %s, 'items', 'created', coalesce(%s, current_timestamp))",
            (run_id, workspace_id, created_at),
        )
        conn.execute(
            "insert into run_batches("
            " run_id, workspace_id, status, items_total, next_index, jobs_created,"
            " created_at, finished_at, archived_at, created_by)"
            " values (%s, %s, %s, %s, %s, %s,"
            " coalesce(%s, current_timestamp), %s, %s, 'tester')",
            (
                run_id,
                workspace_id,
                status,
                items_total,
                items_total if status == "completed" else 0,
                jobs_created,
                created_at,
                finished_at,
                archived_at,
            ),
        )


def test_active_view_lists_feeding_and_queued_with_badge_count(
    client: TestClient,
) -> None:
    ws = _create_workspace(client)
    now = datetime.now(UTC)
    _insert_batch(
        client, ws, "run-active-old", status="running", created_at=now - timedelta(minutes=5)
    )
    _insert_batch(
        client, ws, "run-active-new", status="running", created_at=now - timedelta(minutes=1)
    )
    _insert_batch(client, ws, "run-queued", status="pending", created_at=now)
    _insert_batch(client, ws, "run-paused", status="paused", created_at=now)
    _insert_batch(client, ws, "run-ended", status="completed", finished_at=now - timedelta(hours=1))

    response = client.get(f"/api/workspaces/{ws}/run-batches", params={"view": "active"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_count"] == 4
    ids = [batch["run_id"] for batch in body["batches"]]
    # 进行中（含 paused）按发起时间倒序、排队按提交顺序；终态不出现。
    assert ids == ["run-paused", "run-active-new", "run-active-old", "run-queued"]
    assert body["next_cursor"] is None
    item = body["batches"][0]
    assert item["archive_source"] is None and item["archived_at"] is None
    assert item["created_by"] == "tester"


def test_ended_view_excludes_auto_archived_and_pages_by_keyset(
    client: TestClient,
) -> None:
    ws = _create_workspace(client)
    now = datetime.now(UTC)
    for index in range(3):
        _insert_batch(
            client,
            ws,
            f"run-ended-{index}",
            status="completed",
            finished_at=now - timedelta(hours=index + 1),
            jobs_created=2,
        )
    # 终态且结束超过 7 天：自动归档口径，不进默认（ended）视图。
    _insert_batch(
        client,
        ws,
        "run-auto-archived",
        status="completed",
        finished_at=now - timedelta(days=8),
    )
    # 手动归档的近期终态同样不进 ended 视图。
    _insert_batch(
        client,
        ws,
        "run-manual-archived",
        status="cancelled",
        finished_at=now - timedelta(hours=2),
        archived_at=now - timedelta(minutes=30),
    )

    first = client.get(f"/api/workspaces/{ws}/run-batches", params={"view": "ended", "limit": 2})
    assert first.status_code == 200, first.text
    body = first.json()
    assert [b["run_id"] for b in body["batches"]] == ["run-ended-0", "run-ended-1"]
    assert body["next_cursor"] is not None

    second = client.get(
        f"/api/workspaces/{ws}/run-batches",
        params={"view": "ended", "limit": 2, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    rest = second.json()
    assert [b["run_id"] for b in rest["batches"]] == ["run-ended-2"]
    assert rest["next_cursor"] is None


def test_archived_view_orders_by_archive_time_and_marks_source(
    client: TestClient,
) -> None:
    ws = _create_workspace(client)
    now = datetime.now(UTC)
    _insert_batch(
        client,
        ws,
        "run-auto",
        status="completed",
        finished_at=now - timedelta(days=9),
    )
    _insert_batch(
        client,
        ws,
        "run-manual",
        status="failed",
        finished_at=now - timedelta(days=1),
        archived_at=now - timedelta(hours=3),
    )
    _insert_batch(
        client,
        ws,
        "run-recent-ended",
        status="completed",
        finished_at=now - timedelta(hours=1),
    )

    response = client.get(f"/api/workspaces/{ws}/run-batches", params={"view": "archived"})

    assert response.status_code == 200, response.text
    batches = response.json()["batches"]
    assert [b["run_id"] for b in batches] == ["run-manual", "run-auto"]
    assert batches[0]["archive_source"] == "manual"
    assert batches[0]["archived_at"] is not None
    assert batches[1]["archive_source"] == "auto"
    assert batches[1]["archived_at"] is None


def test_manual_archive_cycle_and_conflict_matrix(client: TestClient) -> None:
    ws = _create_workspace(client)
    other = _create_workspace(client, "run-batches-other")
    now = datetime.now(UTC)
    _insert_batch(client, ws, "run-done", status="completed", finished_at=now - timedelta(hours=1))
    _insert_batch(client, ws, "run-feeding", status="running")
    _insert_batch(
        client,
        other,
        "run-foreign",
        status="completed",
        finished_at=now - timedelta(hours=1),
    )

    archived = client.post(f"/api/workspaces/{ws}/run-batches/run-done/archive")
    assert archived.status_code == 200, archived.text
    batch = archived.json()["batch"]
    assert batch["archived_at"] is not None
    assert batch["archive_source"] == "manual"
    # reviewer r1 P2-1 契约增量：chunk 失败计数与下限标志随列表项下发。
    assert batch["failed_item_count"] == 0
    assert batch["failed_item_count_lower_bound"] is False

    # 归档后从 ended 消失、进 archived；幂等重归档 = 409（不静默成功）。
    ended = client.get(f"/api/workspaces/{ws}/run-batches", params={"view": "ended"}).json()
    assert [b["run_id"] for b in ended["batches"]] == []
    archived_view = client.get(
        f"/api/workspaces/{ws}/run-batches", params={"view": "archived"}
    ).json()
    assert [b["run_id"] for b in archived_view["batches"]] == ["run-done"]
    assert client.post(f"/api/workspaces/{ws}/run-batches/run-done/archive").status_code == 409
    # 非终态不可归档；未知与跨 workspace id 一律 404。
    assert client.post(f"/api/workspaces/{ws}/run-batches/run-feeding/archive").status_code == 409
    assert client.post(f"/api/workspaces/{ws}/run-batches/run-missing/archive").status_code == 404
    assert client.post(f"/api/workspaces/{ws}/run-batches/run-foreign/archive").status_code == 404


def test_list_view_rejects_bad_params(client: TestClient) -> None:
    ws = _create_workspace(client)
    assert (
        client.get(f"/api/workspaces/{ws}/run-batches", params={"view": "later"}).status_code == 422
    )
    assert (
        client.get(
            f"/api/workspaces/{ws}/run-batches",
            params={"view": "ended", "cursor": "not-a-cursor"},
        ).status_code
        == 400
    )
    # 未知 workspace：404（不泄漏存在性）。
    assert client.get("/api/workspaces/run-batches-missing/run-batches").status_code == 404


def test_run_batches_requires_auth(anon_client: TestClient) -> None:
    assert anon_client.get(f"/api/workspaces/{WORKSPACE}/run-batches").status_code == 401
    assert (
        anon_client.post(f"/api/workspaces/{WORKSPACE}/run-batches/run-x/archive").status_code
        == 401
    )
