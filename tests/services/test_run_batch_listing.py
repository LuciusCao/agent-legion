"""run_batch_listing service (#1157 phase3): view semantics + archive guards.

Route-level HTTP contract lives in tests/routes/test_run_batches_api.py;
this file pins the service/query decisions: the auto-archive time boundary,
cursor codec failures, the archive 404/409 split, and the resubmit heal
clearing archived_at (a healed batch is live again, never stuck in the
archived view).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from server.app.services import run_batch_listing
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError

_WORKSPACE = "listing-ws"
_NOW = datetime.now(UTC)


def _seed_workspace(job_db, ws_id: str = _WORKSPACE) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Test') on conflict(id) do nothing",
            (ws_id,),
        )


def _insert_batch(
    job_db,
    run_id: str,
    *,
    status: str,
    workspace_id: str = _WORKSPACE,
    finished_at: datetime | None = None,
    archived_at: datetime | None = None,
    items_total: int = 3,
    processed: int = 0,
    jobs_created: int = 0,
    chunk_errors: list[dict] | None = None,
) -> None:
    _seed_workspace(job_db, workspace_id)
    with job_db.connect() as conn:
        conn.execute(
            "insert into runs(id, workspace_id, source_kind, status) values (%s, %s, 'items', 'created')",
            (run_id, workspace_id),
        )
        conn.execute(
            "insert into run_batches("
            " run_id, workspace_id, status, items_total, next_index, jobs_created,"
            " chunk_errors_json, finished_at, archived_at)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_id,
                workspace_id,
                status,
                items_total,
                processed,
                jobs_created,
                json.dumps(chunk_errors or []),
                finished_at,
                archived_at,
            ),
        )


def test_auto_archive_boundary_is_seven_days(job_db) -> None:
    """finished_at 恰好落在窗口内的一侧进 ended，另一侧进 archived(auto)。"""
    inside = _NOW - timedelta(days=7) + timedelta(minutes=1)
    outside = _NOW - timedelta(days=7) - timedelta(minutes=1)
    _insert_batch(job_db, "run-inside", status="completed", finished_at=inside)
    _insert_batch(job_db, "run-outside", status="completed", finished_at=outside)

    ended = run_batch_listing.list_run_batches(
        job_db, _WORKSPACE, view="ended", limit=20, cursor=None
    )
    assert [b["run_id"] for b in ended["batches"]] == ["run-inside"]
    archived = run_batch_listing.list_run_batches(
        job_db, _WORKSPACE, view="archived", limit=20, cursor=None
    )
    assert [b["run_id"] for b in archived["batches"]] == ["run-outside"]
    assert archived["batches"][0]["archive_source"] == "auto"


def test_bad_cursor_raises_400_family(job_db) -> None:
    _seed_workspace(job_db)
    for bad in ("no-pipe", "2026-13-99T00:00:00+00:00|run-x", "|run-x"):
        with pytest.raises(InvalidOperationError):
            run_batch_listing.list_run_batches(
                job_db, _WORKSPACE, view="ended", limit=20, cursor=bad
            )
    # 空串 cursor 等价于未传（首页），不报错。
    first = run_batch_listing.list_run_batches(
        job_db, _WORKSPACE, view="ended", limit=20, cursor=""
    )
    assert first["batches"] == []
    # active 视图不分页：带 cursor 即错。
    with pytest.raises(InvalidOperationError):
        run_batch_listing.list_run_batches(
            job_db, _WORKSPACE, view="active", limit=20, cursor=f"{_NOW.isoformat()}|run-x"
        )


def test_unknown_view_and_workspace(job_db) -> None:
    _seed_workspace(job_db)
    with pytest.raises(InvalidOperationError):
        run_batch_listing.list_run_batches(job_db, _WORKSPACE, view="later", limit=20, cursor=None)
    with pytest.raises(NotFoundError):
        run_batch_listing.list_run_batches(
            job_db, "missing-ws", view="active", limit=20, cursor=None
        )


def test_archive_service_404_and_409(job_db) -> None:
    _insert_batch(job_db, "run-open", status="running")
    _insert_batch(
        job_db,
        "run-foreign",
        workspace_id="listing-other",
        status="completed",
        finished_at=_NOW,
    )

    with pytest.raises(NotFoundError):
        run_batch_listing.archive_run_batch(job_db, _WORKSPACE, "run-missing")
    with pytest.raises(NotFoundError):
        run_batch_listing.archive_run_batch(job_db, _WORKSPACE, "run-foreign")
    with pytest.raises(ConflictError):
        run_batch_listing.archive_run_batch(job_db, _WORKSPACE, "run-open")

    _insert_batch(job_db, "run-done", status="failed", finished_at=_NOW)
    archived = run_batch_listing.archive_run_batch(job_db, _WORKSPACE, "run-done")
    assert archived["batch"]["archive_source"] == "manual"
    assert archived["batch"]["archived_at"] is not None
    with pytest.raises(ConflictError):
        run_batch_listing.archive_run_batch(job_db, _WORKSPACE, "run-done")


def test_failed_item_count_from_chunk_errors(job_db) -> None:
    """reviewer r1 P2-1：chunk 失败条目数按 chunk_start + 500 推导、以
    items_total 钳制、打满 20 条标下限；缺 chunk_start 的异形条目不炸。"""
    _insert_batch(
        job_db,
        "run-partial",
        status="completed",
        items_total=3000,
        processed=3000,
        jobs_created=2788,
        chunk_errors=[{"chunk_start": 2800, "error": "外部连接超时"}],
        finished_at=_NOW,
    )
    _insert_batch(
        job_db,
        "run-saturated",
        status="completed",
        items_total=12000,
        processed=12000,
        jobs_created=2000,
        chunk_errors=[{"chunk_start": i * 500, "error": "x"} for i in range(20)],
        finished_at=_NOW,
    )
    _insert_batch(
        job_db,
        "run-legacy-shape",
        status="completed",
        items_total=100,
        processed=100,
        jobs_created=90,
        chunk_errors=[{"index": 3, "error": "无 chunk_start 的旧形状"}],
        finished_at=_NOW,
    )

    ended = run_batch_listing.list_run_batches(
        job_db, _WORKSPACE, view="ended", limit=20, cursor=None
    )
    by_id = {b["run_id"]: b for b in ended["batches"]}
    # min(500, 3000-2800) = 200；跳过（已有任务）= 3000-2788-200 = 12。
    assert by_id["run-partial"]["failed_item_count"] == 200
    assert by_id["run-partial"]["failed_item_count_lower_bound"] is False
    # 末条 chunk_start=9500：min(500, 12000-9500)=500；20 条打满 → 下限。
    assert by_id["run-saturated"]["failed_item_count"] == 10000
    assert by_id["run-saturated"]["failed_item_count_lower_bound"] is True
    # 无 chunk_start 的条目计 0（钳制/异形防御），不影响列出。
    assert by_id["run-legacy-shape"]["failed_item_count"] == 0


def test_resubmit_heal_clears_archived_at(job_db) -> None:
    """failed+archived 的 batch 被同 digest 重提治愈后重回 active，归档位清空。"""
    run_id = "run-heal"
    _insert_batch(job_db, run_id, status="failed", finished_at=_NOW, archived_at=_NOW)
    spec = {"items": [{"type": "ref", "connection_key": "c", "external_id": "e"}]}

    reset = job_db.reset_run_batch_for_resubmit(run_id, spec, 1)

    assert reset is not None
    assert reset["status"] == "pending"
    assert reset["archived_at"] is None
    active = run_batch_listing.list_run_batches(
        job_db, _WORKSPACE, view="active", limit=20, cursor=None
    )
    assert [b["run_id"] for b in active["batches"]] == [run_id]
    archived = run_batch_listing.list_run_batches(
        job_db, _WORKSPACE, view="archived", limit=20, cursor=None
    )
    assert archived["batches"] == []
