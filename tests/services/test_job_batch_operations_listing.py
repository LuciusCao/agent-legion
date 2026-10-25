"""job_batch_operations listing/archive service (M61): view semantics + archive guards.

Route-level HTTP contract lives in tests/routes/test_job_batch_operations.py;
this file pins the service/query decisions against the run_batches (#1157
phase3) reference semantics: the auto-archive time boundary, cursor codec
failures, the archive 404/409 split plus the idempotent replay (M61 brief:
重复归档返回现态不报错, diverging from run_batches' 409), the page-wide
reason-code grouping, and the 90-day retention sweep edges (clock-injected,
no sleep).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from server.app.services.job_batch_operations import listing
from server.app.services.job_errors import ConflictError, InvalidOperationError, NotFoundError

_WORKSPACE = "ops-listing-ws"


def _now() -> datetime:
    """执行时点现读时钟，绝不在模块 import 时冻结：CI xdist 先全量
    collection（导入本模块）再执行，两者相隔分钟级，import 时戳会让
    7 天边界两侧的整体漂移出窗口（main 红 run 39561162339）。"""
    return datetime.now(UTC)


def _seed_workspace(job_db, ws_id: str = _WORKSPACE) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Test') on conflict(id) do nothing",
            (ws_id,),
        )


def _insert_operation(
    job_db,
    op_id: str,
    *,
    status: str,
    workspace_id: str = _WORKSPACE,
    kind: str = "pause",
    finished_at: datetime | None = None,
    archived_at: datetime | None = None,
    created_at: datetime | None = None,
    items_total: int = 2,
    processed: int | None = None,
    target_spec: dict | None = None,
) -> None:
    _seed_workspace(job_db, workspace_id)
    with job_db.connect() as conn:
        conn.execute(
            "insert into job_batch_operations("
            " id, workspace_id, kind, status, items_total, next_index, target_spec_json,"
            " created_at, finished_at, archived_at, created_by)"
            " values (%s, %s, %s, %s, %s, %s, %s,"
            " coalesce(%s, current_timestamp), %s, %s, 'tester')",
            (
                op_id,
                workspace_id,
                kind,
                status,
                items_total,
                (items_total if status == "completed" else 0) if processed is None else processed,
                json.dumps(target_spec or {}),
                created_at,
                finished_at,
                archived_at,
            ),
        )


def _list(job_db, settings, view: str, limit: int = 20, cursor: str | None = None) -> dict:
    return listing.list_job_batch_operations(
        job_db, _WORKSPACE, settings=settings, view=view, limit=limit, cursor=cursor
    )


def test_auto_archive_boundary_is_seven_days(job_db, settings) -> None:
    """finished_at 恰好落在窗口内的一侧进 ended，另一侧进 archived(auto)。"""
    # 余量取 1 小时而非分钟级（run_batches 同形测试的既有纪律）：测试读时钟
    # 与 service 读时钟之间隔着整段 seed/查询，collection→execution 漂移永远
    # 不该能把两侧推出各自窗口。
    now = _now()
    inside = now - timedelta(days=7) + timedelta(hours=1)
    outside = now - timedelta(days=7) - timedelta(hours=1)
    _insert_operation(job_db, "op-inside", status="completed", finished_at=inside)
    _insert_operation(job_db, "op-outside", status="completed", finished_at=outside)

    ended = _list(job_db, settings, "ended")
    assert [op["id"] for op in ended["operations"]] == ["op-inside"]
    archived = _list(job_db, settings, "archived")
    assert [op["id"] for op in archived["operations"]] == ["op-outside"]
    assert archived["operations"][0]["archive_source"] == "auto"


def test_bad_cursor_raises_400_family(job_db, settings) -> None:
    _seed_workspace(job_db)
    for bad in ("no-pipe", "2026-13-99T00:00:00+00:00|op-x", "|op-x"):
        with pytest.raises(InvalidOperationError):
            _list(job_db, settings, "ended", cursor=bad)
    # 空串 cursor 等价于未传（首页），不报错。
    assert _list(job_db, settings, "ended", cursor="")["operations"] == []
    # active 视图不分页：带 cursor 即错。
    with pytest.raises(InvalidOperationError):
        _list(job_db, settings, "active", cursor=f"{_now().isoformat()}|op-x")


def test_unknown_view_and_workspace(job_db, settings) -> None:
    _seed_workspace(job_db)
    with pytest.raises(InvalidOperationError):
        _list(job_db, settings, "later")
    with pytest.raises(NotFoundError):
        listing.list_job_batch_operations(
            job_db, "missing-ws", settings=settings, view="active", limit=20, cursor=None
        )


def test_active_view_orders_queued_then_in_flight_and_counts(job_db, settings) -> None:
    """进行中（含 paused）按发起时间倒序、排队按提交顺序；终态不出现；
    active_count 与视图无关。"""
    now = _now()
    _insert_operation(
        job_db, "op-running-old", status="running", created_at=now - timedelta(minutes=5)
    )
    _insert_operation(
        job_db, "op-running-new", status="running", created_at=now - timedelta(minutes=1)
    )
    _insert_operation(job_db, "op-queued", status="pending", created_at=now)
    _insert_operation(job_db, "op-paused", status="paused", created_at=now)
    _insert_operation(job_db, "op-ended", status="completed", finished_at=now - timedelta(hours=1))

    active = _list(job_db, settings, "active")
    assert [op["id"] for op in active["operations"]] == [
        "op-paused",
        "op-running-new",
        "op-running-old",
        "op-queued",
    ]
    assert active["active_count"] == 4
    assert active["next_cursor"] is None
    item = active["operations"][0]
    assert item["archived_at"] is None and item["archive_source"] is None
    # 列表项携带完整 #946 句柄字段（卡片与轮询同形）。
    assert item["kind"] == "pause" and item["created_by"] == "tester"
    assert item["reason_counts"] == {}
    # ended 视图同快照读同一徽标口径。
    assert _list(job_db, settings, "ended")["active_count"] == 4


def test_ended_view_pages_by_keyset(job_db, settings) -> None:
    now = _now()
    for index in range(3):
        _insert_operation(
            job_db,
            f"op-ended-{index}",
            status="completed",
            finished_at=now - timedelta(hours=index + 1),
        )
    _insert_operation(
        job_db, "op-auto-archived", status="completed", finished_at=now - timedelta(days=8)
    )
    _insert_operation(
        job_db,
        "op-manual-archived",
        status="completed",
        finished_at=now - timedelta(hours=1),
        archived_at=now,
    )

    page1 = _list(job_db, settings, "ended", limit=2)
    assert [op["id"] for op in page1["operations"]] == ["op-ended-0", "op-ended-1"]
    assert page1["next_cursor"] is not None
    page2 = _list(job_db, settings, "ended", limit=2, cursor=page1["next_cursor"])
    assert [op["id"] for op in page2["operations"]] == ["op-ended-2"]
    # 短页不再发游标（瀑布流结束）。
    assert page2["next_cursor"] is None


def test_archived_view_mixes_manual_and_auto_in_coalesce_order(job_db, settings) -> None:
    now = _now()
    _insert_operation(
        job_db,
        "op-auto-old",
        status="cancelled",
        finished_at=now - timedelta(days=10),
    )
    _insert_operation(
        job_db,
        "op-manual-recent",
        status="completed",
        finished_at=now - timedelta(days=1),
        archived_at=now - timedelta(hours=2),
    )

    archived = _list(job_db, settings, "archived")
    assert [op["id"] for op in archived["operations"]] == ["op-manual-recent", "op-auto-old"]
    by_id = {op["id"]: op for op in archived["operations"]}
    assert by_id["op-manual-recent"]["archive_source"] == "manual"
    assert by_id["op-auto-old"]["archive_source"] == "auto"


def test_list_page_groups_reason_counts_in_one_query(job_db, settings) -> None:
    """列表页的 reason_counts 来自一次 group-by（不逐行查）：有结果行的 op
    拿到分解，无结果行的 op 拿空 dict。"""
    now = _now()
    _insert_operation(job_db, "op-with-results", status="completed", finished_at=now)
    _insert_operation(job_db, "op-no-results", status="completed", finished_at=now)
    with job_db.connect() as conn:
        for seq, (status, reason) in enumerate(
            [("succeeded", None), ("failed", "not_found"), ("skipped", "not_found")]
        ):
            conn.execute(
                "insert into job_batch_operation_results("
                " op_id, seq, job_id, status, reason_code, payload_json)"
                " values ('op-with-results', %s, %s, %s, %s, '{}')",
                (seq, f"job-{seq}", status, reason),
            )

    ended = _list(job_db, settings, "ended")
    by_id = {op["id"]: op for op in ended["operations"]}
    assert by_id["op-with-results"]["reason_counts"] == {"not_found": 2}
    assert by_id["op-no-results"]["reason_counts"] == {}
    # 计数器列原样带出（卡片进度条）。
    assert by_id["op-with-results"]["processed_count"] == 2


def test_archive_404_409_and_idempotent_replay(job_db, settings) -> None:
    _insert_operation(job_db, "op-open", status="running")
    _insert_operation(
        job_db,
        "op-foreign",
        workspace_id="ops-listing-other",
        status="completed",
        finished_at=_now(),
    )

    with pytest.raises(NotFoundError):
        listing.archive_job_batch_operation(job_db, _WORKSPACE, "op-missing")
    with pytest.raises(NotFoundError):
        listing.archive_job_batch_operation(job_db, _WORKSPACE, "op-foreign")
    with pytest.raises(ConflictError):
        listing.archive_job_batch_operation(job_db, _WORKSPACE, "op-open")

    _insert_operation(job_db, "op-done", status="failed", finished_at=_now())
    archived = listing.archive_job_batch_operation(job_db, _WORKSPACE, "op-done")
    assert archived["operation"]["archive_source"] == "manual"
    first_stamp = archived["operation"]["archived_at"]
    assert first_stamp is not None
    # 幂等（M61 brief，run_batches 此处为 409）：重复归档返回现态，不报错、
    # 不重置 archived_at。
    replay = listing.archive_job_batch_operation(job_db, _WORKSPACE, "op-done")
    assert replay["operation"]["archived_at"] == first_stamp
    assert replay["operation"]["archive_source"] == "manual"


def test_sweep_window_edges_and_results_cascade(job_db, settings) -> None:
    """归档后 90 天物理删除：manual 按 archived_at、auto 按 finished_at+7d+90d；
    窗口内与非终态存活；结果子表随 FK 级联。时钟注入，禁 sleep。"""
    moment = _now()
    _insert_operation(
        job_db,
        "op-manual-old",
        status="completed",
        finished_at=moment - timedelta(days=95),
        archived_at=moment - timedelta(days=91),
    )
    _insert_operation(
        job_db,
        "op-manual-recent",
        status="completed",
        finished_at=moment - timedelta(days=20),
        archived_at=moment - timedelta(days=10),
    )
    _insert_operation(
        job_db,
        "op-auto-old",
        status="cancelled",
        finished_at=moment - timedelta(days=98),
    )
    _insert_operation(
        job_db,
        "op-auto-in-window",
        status="completed",
        finished_at=moment - timedelta(days=96),
    )
    _insert_operation(job_db, "op-still-running", status="running")
    with job_db.connect() as conn:
        conn.execute(
            "insert into job_batch_operation_results(op_id, seq, job_id, status)"
            " values ('op-manual-old', 0, 'job-1', 'succeeded')",
        )

    deleted = listing.sweep_archived_job_batch_operations(job_db, moment)

    assert deleted == 2
    with job_db._connect_read() as conn:
        remaining = {
            str(row["id"]) for row in conn.execute("select id from job_batch_operations").fetchall()
        }
        results_left = conn.execute(
            "select count(*) as c from job_batch_operation_results"
        ).fetchone()["c"]
    assert remaining == {"op-manual-recent", "op-auto-in-window", "op-still-running"}
    assert results_left == 0
    # 幂等：第二趟无可删。
    assert listing.sweep_archived_job_batch_operations(job_db, moment) == 0


def test_sweep_accepts_bare_dsn(job_db, settings) -> None:
    """sweeper 入口持裸 DSN（#187 ConnectSource）：from_dsn 适配器同形工作。
    DSN 取自 facade 的 data-layer-private ``_path``（不 import
    tests.postgres_support——直接 import 会触发 postgres 清单登记要求，
    本文件经 job_db fixture 已隐式落位 postgres 层）。"""
    moment = _now()
    _insert_operation(
        job_db,
        "op-dsn-old",
        status="completed",
        finished_at=moment - timedelta(days=95),
        archived_at=moment - timedelta(days=91),
    )
    assert listing.sweep_archived_job_batch_operations(job_db._path, moment) == 1


def test_active_view_gates_only_work_creating_unfinished_operations(
    job_db, settings, monkeypatch
) -> None:
    settings.executor_runtime.workflows.max_items_per_run = 100
    _insert_operation(job_db, "op-gated", status="running", kind="rerun")
    _insert_operation(job_db, "op-ungated-kind", status="running", kind="pause")
    _insert_operation(job_db, "op-paused", status="paused", kind="rerun")
    _insert_operation(job_db, "op-cursor-complete", status="running", kind="rerun", processed=2)
    _insert_operation(
        job_db,
        "op-stream-waiting",
        status="running",
        kind="rerun",
        processed=2,
        target_spec={"job_ids": [], "stream": {"next_cursor": "job-200"}},
    )
    _insert_operation(
        job_db,
        "op-stream-drained",
        status="running",
        kind="rerun",
        processed=2,
        target_spec={"job_ids": [], "stream": {"next_cursor": None}},
    )
    _insert_operation(job_db, "op-ended", status="completed", kind="rerun", finished_at=_now())
    calls = 0

    def counted_backlog(workspace_id: str) -> dict[str, int]:
        nonlocal calls
        calls += 1
        assert workspace_id == _WORKSPACE
        return {"pending": 100, "running": 0, "completed": 500}

    monkeypatch.setattr(job_db, "count_jobs_by_status", counted_backlog)

    active = _list(job_db, settings, "active")

    by_id = {operation["id"]: operation for operation in active["operations"]}
    assert by_id["op-gated"]["feeding_gated"] is True
    assert by_id["op-ungated-kind"]["feeding_gated"] is False
    assert by_id["op-paused"]["feeding_gated"] is False
    assert by_id["op-cursor-complete"]["feeding_gated"] is False
    assert by_id["op-stream-waiting"]["feeding_gated"] is True
    assert by_id["op-stream-drained"]["feeding_gated"] is False
    assert all(operation["workspace_backlog"] == 100 for operation in active["operations"])
    assert all(operation["watermark"] == 100 for operation in active["operations"])
    assert calls == 1
    _list(job_db, settings, "ended")
    assert calls == 1
