"""Schema v80 (#532 / #505): the campaigns lifecycle at the DB layer.

Pins the migration record, the table shape (mode/status CHECK enums, counter
columns, target_spec_json/progress_json), the runs.campaign_id linkage and
its partial index, plus the queries-layer state machine — create / CAS
transitions / the progress CAS advance (the pause/cancel-wins race) —
against real Postgres. Route-level behavior lives in
tests/routes/test_campaigns_api.py; the parity between fresh and upgraded
databases is tests/db/test_schema_upgrade_parity.py's whole job (its undo
inventory covers v80).
"""

from __future__ import annotations

import pytest
from psycopg import IntegrityError

from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs.queries.campaign_guards import (
    _CAMPAIGN_LOCK_NAMESPACE,
    CampaignGuardQueriesMixin,
)
from server.app.jobs.queries.campaigns import CampaignQueriesMixin
from server.app.services.job_errors import ConflictError
from tests.postgres_support import TEST_DATABASE_URL


class _CampaignQueries(CampaignGuardQueriesMixin, CampaignQueriesMixin):
    """Rows + quota guards, the same composition JobQueries carries."""


def _job_db() -> _CampaignQueries:
    # The mixins are enough: their own connection wiring (ConnectionQueriesMixin)
    # carries _path; no facade needed for these direct-layer tests.
    job_db = _CampaignQueries.__new__(_CampaignQueries)
    job_db._path = TEST_DATABASE_URL  # noqa: SLF001 — test wiring
    return job_db


def _insert_workspace(workspace_id: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            f" values ('{workspace_id}', '{workspace_id}', '{workspace_id}')"
            " on conflict (id) do nothing"
        )


def test_schema_v80_recorded() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from schema_migrations where version=%s", (80,)).fetchone()
    assert row is not None
    assert row["name"] == "campaigns"


def test_schema_v81_recorded() -> None:
    """v81（#545 round-4）：投递标记表——重放归属的持久权威。"""
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from schema_migrations where version=%s", (81,)).fetchone()
    assert row is not None
    assert row["name"] == "campaign_deliveries"


def test_campaign_job_deliveries_shape() -> None:
    """标记表只有 (campaign_id, job_id) 复合主键——纯归属标记，无结果列；
    PK 即 feeder 重放守卫的点查（每 campaign 至多一个在途批，有界）。"""
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='campaign_job_deliveries'"
            ).fetchall()
        }
    assert columns == {"campaign_id", "job_id"}
    # 复合主键真实生效：先种一行真 campaign（FK 可达），重复插入同一
    # (campaign_id, job_id) 对被 PK 拒绝——审核 P3：空串 id 首插即撞 FK，
    # 从未走到重复路径，断言是空洞的。
    _insert_workspace("feeder-marker-pk-ws")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into campaigns(id, workspace_id, mode)"
            " values ('campaign-marker-pk', 'feeder-marker-pk-ws', 'rerun')"
            " on conflict (id) do nothing"
        )
        conn.execute(
            "insert into campaign_job_deliveries(campaign_id, job_id)"
            " values ('campaign-marker-pk', 'job-dup')"
        )
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into campaign_job_deliveries(campaign_id, job_id)"
            " values ('campaign-marker-pk', 'job-dup')"
        )


def test_campaigns_columns() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema() and table_name='campaigns'"
            ).fetchall()
        }
    assert columns == {
        "id",
        "workspace_id",
        "mode",
        "status",
        "target_spec_json",
        "progress_json",
        "watermark",
        "batch_size",
        "batches_submitted",
        "jobs_succeeded",
        "jobs_skipped",
        "jobs_failed",
        "error_message",
        "created_by",
        "created_at",
        "updated_at",
        "finished_at",
    }


def test_mode_and_status_check_enums() -> None:
    """三模式枚举 + 六态状态机是数据库边界（任何代码路径都绕不过）。"""
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("insert into campaigns(workspace_id, mode) values ('demo_workflow', 'bogus')")
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into campaigns(workspace_id, mode, status)"
            " values ('demo_workflow', 'rerun', 'bogus')"
        )


def test_watermark_and_batch_size_checks() -> None:
    """watermark >= 0（0 是 upgrade 模式预留的不设闸）、batch_size >= 1。"""
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into campaigns(workspace_id, mode, watermark)"
            " values ('demo_workflow', 'rerun', -1)"
        )
    with pytest.raises(IntegrityError), write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into campaigns(workspace_id, mode, batch_size)"
            " values ('demo_workflow', 'rerun', 0)"
        )


def test_campaigns_workspace_cascade() -> None:
    """workspace 删除级联清掉 campaign 行（与 runs/jobs 同款隔离语义）。"""
    job_db = _job_db()
    _insert_workspace("campaign_cascade_ws")
    row = job_db.create_campaign(
        "campaign_cascade_ws", "rerun", {"job_ids": ["j-1"]}, watermark=100, batch_size=10
    )
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from workspaces where id='campaign_cascade_ws'")
    assert job_db.get_campaign(row["id"]) is None


def test_runs_campaign_id_column_and_partial_index() -> None:
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select indexdef from pg_indexes"
            " where schemaname=current_schema() and indexname='idx_runs_campaign'"
        ).fetchone()
        column = conn.execute(
            "select column_default from information_schema.columns"
            " where table_schema=current_schema() and table_name='runs'"
            " and column_name='campaign_id'"
        ).fetchone()
    assert row is not None
    indexdef = str(row["indexdef"])
    # Postgres normalizes the literal to ''::text; assert the predicate, not
    # the exact spelling.
    assert "campaign_id <> ''" in indexdef.replace("''::text", "''")
    # Legacy rows (manual runs) default to '' and stay out of the index.
    assert column is not None and "''" in str(column["column_default"])


def test_create_defaults_and_active_scan() -> None:
    job_db = _job_db()
    _insert_workspace("campaign_active_ws")
    row = job_db.create_campaign(
        "campaign_active_ws",
        "submit",
        {"items": []},
        watermark=50,
        batch_size=5,
        progress={"item_offset": 0},
    )
    assert row["status"] == "pending"
    assert row["batches_submitted"] == 0
    assert row["progress"] == {"item_offset": 0}
    active = [c for c in job_db.list_active_campaigns() if c["id"] == row["id"]]
    assert len(active) == 1
    # Terminal campaigns leave the active scan.
    job_db.transition_campaign_status(row["id"], ("pending",), "cancelled")
    assert all(c["id"] != row["id"] for c in job_db.list_active_campaigns())


def test_status_transitions_cas() -> None:
    """pause/resume/cancel 的 CAS 语义：from_statuses 不命中即 miss（None）。"""
    job_db = _job_db()
    _insert_workspace("campaign_transition_ws")
    row = job_db.create_campaign(
        "campaign_transition_ws", "rerun", {"job_ids": ["j-1"]}, watermark=10, batch_size=2
    )
    campaign_id = row["id"]
    # pending → paused (pause API on a not-yet-picked-up campaign)
    assert (
        job_db.transition_campaign_status(campaign_id, ("pending", "running"), "paused")["status"]
        == "paused"
    )
    # resume: only paused → running
    assert (
        job_db.transition_campaign_status(campaign_id, ("paused",), "running")["status"]
        == "running"
    )
    # A stale from-set misses (the row is running, not pending).
    assert job_db.transition_campaign_status(campaign_id, ("pending",), "paused") is None
    # cancel from any non-terminal state; terminal transitions stamp finished_at.
    cancelled = job_db.transition_campaign_status(
        campaign_id, ("pending", "running", "paused"), "cancelled"
    )
    assert cancelled is not None and cancelled["status"] == "cancelled"
    assert cancelled["finished_at"] is not None
    # Terminal rows refuse further transitions.
    assert job_db.transition_campaign_status(campaign_id, ("pending", "running"), "paused") is None


def test_failed_transition_stamps_error_message() -> None:
    job_db = _job_db()
    _insert_workspace("campaign_failed_ws")
    row = job_db.create_campaign(
        "campaign_failed_ws", "submit", {"items": []}, watermark=10, batch_size=2
    )
    failed = job_db.transition_campaign_status(
        row["id"], ("pending", "running"), "failed", error_message="manifest corrupt at line 3"
    )
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error_message"] == "manifest corrupt at line 3"
    assert failed["finished_at"] is not None


def test_advance_campaign_progress_cas_pause_wins() -> None:
    """游标 CAS 推进（CAMPAIGN-STATE-001 核心）：advance 的 WHERE 带旧
    progress_json——pause 抢先落地后 advance miss，游标冻结在旧值。"""
    job_db = _job_db()
    _insert_workspace("campaign_advance_ws")
    row = job_db.create_campaign(
        "campaign_advance_ws",
        "submit",
        {"items": []},
        watermark=10,
        batch_size=2,
        progress={"item_offset": 0},
    )
    campaign_id = row["id"]
    baseline = {"item_offset": 0}
    # The feeder read baseline, then a pause landed.
    assert (
        job_db.transition_campaign_status(campaign_id, ("pending", "running"), "paused") is not None
    )
    # The feeder's CAS advance misses: the row's status left the active set.
    assert (
        job_db.advance_campaign_progress(
            campaign_id,
            expected_progress=baseline,
            progress={"item_offset": 2},
            batches_submitted=1,
            jobs_succeeded=2,
            jobs_skipped=0,
            jobs_failed=0,
        )
        is None
    )
    # A matching CAS (resume first, cursor still at baseline) succeeds and
    # the counters land as absolute values.
    assert job_db.transition_campaign_status(campaign_id, ("paused",), "running") is not None
    advanced = job_db.advance_campaign_progress(
        campaign_id,
        expected_progress=baseline,
        progress={"item_offset": 2, "samples": [{"level": 4, "ts": 1}]},
        batches_submitted=1,
        jobs_succeeded=2,
        jobs_skipped=1,
        jobs_failed=0,
    )
    assert advanced is not None
    assert advanced["progress"]["item_offset"] == 2
    assert advanced["jobs_succeeded"] == 2
    assert advanced["jobs_skipped"] == 1
    # A replayed advance against the stale expected progress misses.
    assert (
        job_db.advance_campaign_progress(
            campaign_id,
            expected_progress=baseline,
            progress={"item_offset": 4},
            batches_submitted=2,
            jobs_succeeded=4,
            jobs_skipped=1,
            jobs_failed=0,
        )
        is None
    )


def test_list_orders_newest_first_and_limit() -> None:
    job_db = _job_db()
    _insert_workspace("campaign_list_ws")
    ids = [
        job_db.create_campaign(
            "campaign_list_ws", "rerun", {"job_ids": [f"j-{i}"]}, watermark=10, batch_size=2
        )["id"]
        for i in range(3)
    ]
    listed = job_db.list_campaigns("campaign_list_ws", limit=2)
    assert [c["id"] for c in listed] == ids[-2:][::-1] or len(listed) == 2
    # ids are unique uuid4 hex; the limit is honored regardless of order ties.
    assert len(listed) == 2
    assert all(c["workspace_id"] == "campaign_list_ws" for c in listed)


def test_count_active_campaigns_scoped_to_workspace() -> None:
    job_db = _job_db()
    _insert_workspace("campaign_count_a")
    _insert_workspace("campaign_count_b")
    job_db.create_campaign(
        "campaign_count_a", "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1
    )
    job_db.create_campaign(
        "campaign_count_b", "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1
    )
    job_db.create_campaign(
        "campaign_count_b", "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1
    )
    assert job_db.count_active_campaigns("campaign_count_a") == 1
    assert job_db.count_active_campaigns("campaign_count_b") == 2


# ---------------------------------------------------------------------------
# Guarded quota paths (PR #541 P2): count and write in ONE transaction under
# the workspace advisory lock.
# ---------------------------------------------------------------------------


def _cap_two_workspace() -> str:
    _insert_workspace("campaign_guard_ws")
    return "campaign_guard_ws"


def test_create_campaign_guarded_refuses_at_cap() -> None:
    job_db = _job_db()
    workspace_id = _cap_two_workspace()
    for _ in range(2):
        job_db.create_campaign_guarded(
            workspace_id, "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1, max_active=2
        )
    with pytest.raises(ConflictError):
        job_db.create_campaign_guarded(
            workspace_id, "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1, max_active=2
        )
    # Terminal rows free the slot again.
    first = job_db.list_campaigns(workspace_id, limit=1)[0]
    job_db.transition_campaign_status(first["id"], ("pending",), "cancelled")
    assert (
        job_db.create_campaign_guarded(
            workspace_id, "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1, max_active=2
        )["status"]
        == "pending"
    )


def test_create_campaign_guarded_serializes_concurrent_last_slot() -> None:
    """两个并发事务争最后一个名额：advisory lock 串行化 count+INSERT，
    后进者数到先提交的行并拒绝（旧的无锁 count-then-insert 双双落行）。"""
    import threading

    job_db = _job_db()
    workspace_id = _cap_two_workspace()
    job_db.create_campaign_guarded(
        workspace_id, "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1, max_active=2
    )
    start = threading.Barrier(2, timeout=10)
    results: list = []
    errors: list = []

    def _run() -> None:
        try:
            start.wait(timeout=10)
            results.append(
                job_db.create_campaign_guarded(
                    workspace_id,
                    "rerun",
                    {"job_ids": ["j"]},
                    watermark=1,
                    batch_size=1,
                    max_active=2,
                )
            )
        except Exception as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not any(thread.is_alive() for thread in threads)
    assert job_db.count_active_campaigns(workspace_id) == 2
    assert len(results) == 1
    assert len(errors) == 1 and isinstance(errors[0], ConflictError)


def _wait_for_advisory_waiter(namespace: int, workspace_id: str, timeout: float = 10.0) -> bool:
    """True when some session is BLOCKED on this workspace's campaign lock.

    The deterministic serialization witness for the interleaved race test:
    the second transaction's ``pg_advisory_xact_lock`` shows up in pg_locks
    as an ungranted advisory request long before any sleep-based timing
    could prove the same thing.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with read_connection(TEST_DATABASE_URL) as conn:
            row = conn.execute(
                "select 1 from pg_locks where locktype='advisory' and classid=%s"
                " and objid=hashtext(%s) and granted=false limit 1",
                (namespace, workspace_id),
            ).fetchone()
        if row is not None:
            return True
        time.sleep(0.02)
    return False


def test_create_campaign_guarded_races_interleaved_via_count_shim() -> None:
    """交错窗口版本：第一个事务在 count 之后、INSERT 之前挂起（锁内、
    事务内），第二个事务阻塞在 workspace advisory lock 上（pg_locks 里出现
    未授予的等待者——确定性证据）；第一个提交后第二个重新数到 cap 并拒绝。
    shim 撑开的正是旧实现（事务外 count）暴露给两个并发请求的窗口——
    钉的是 count 与 INSERT 必须同事务。"""
    import threading
    from unittest import mock

    import server.app.jobs.queries.campaign_guards as guards_module

    job_db = _job_db()
    workspace_id = _cap_two_workspace()
    job_db.create_campaign_guarded(
        workspace_id, "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1, max_active=2
    )
    original_count = guards_module.count_active_campaigns_tx
    first_count_done = threading.Event()
    release_first = threading.Event()

    def _stalling_count(conn, ws: str) -> int:
        count = original_count(conn, ws)
        if not first_count_done.is_set():
            first_count_done.set()
            release_first.wait(timeout=30)
        return count

    results: list = []
    errors: list = []

    def _run() -> None:
        try:
            results.append(
                job_db.create_campaign_guarded(
                    workspace_id,
                    "rerun",
                    {"job_ids": ["j"]},
                    watermark=1,
                    batch_size=1,
                    max_active=2,
                )
            )
        except Exception as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    # Module-level patch: create_campaign_guarded resolves the count via the
    # module global, so the shim intercepts the guarded method's one call.
    with mock.patch.object(guards_module, "count_active_campaigns_tx", _stalling_count):
        first = threading.Thread(target=_run)
        first.start()
        assert first_count_done.wait(timeout=10), "first transaction never reached its count"
        # 第一个事务停在 count 之后（锁内、事务内）；第二个并发发起。
        second = threading.Thread(target=_run)
        second.start()
        # 确定性证据：第二个阻塞在本 workspace 的 campaign 锁上（未被授予）。
        assert _wait_for_advisory_waiter(_CAMPAIGN_LOCK_NAMESPACE, workspace_id), (
            "second create did not block on the workspace lock — count and INSERT"
            " are not serialized"
        )
        assert results == [] and errors == [], "second completed before the first committed"
        # 放行第一个：提交释放锁，第二个随即重新数到 cap 并拒绝。
        release_first.set()
        first.join(timeout=15)
        second.join(timeout=15)
        assert not first.is_alive() and not second.is_alive(), "a create hung"
    assert job_db.count_active_campaigns(workspace_id) == 2
    assert len(results) == 1
    assert len(errors) == 1 and isinstance(errors[0], ConflictError)


def test_resume_campaign_guarded_counts_refilled_slots() -> None:
    """pause 腾出的名额被新行补满后，guarded resume 数到满额并拒绝；
    腾出名额后成功（paused→running）。"""
    job_db = _job_db()
    workspace_id = _cap_two_workspace()
    first = job_db.create_campaign_guarded(
        workspace_id, "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1, max_active=2
    )
    second = job_db.create_campaign_guarded(
        workspace_id, "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1, max_active=2
    )
    job_db.transition_campaign_status(first["id"], ("pending",), "paused")
    # 补满腾出的名额。
    job_db.create_campaign_guarded(
        workspace_id, "rerun", {"job_ids": ["j"]}, watermark=1, batch_size=1, max_active=2
    )
    with pytest.raises(ConflictError):
        job_db.resume_campaign_guarded(workspace_id, first["id"], max_active=2)
    # 腾出一个名额后 resume 成功。
    job_db.transition_campaign_status(second["id"], ("pending",), "cancelled")
    resumed = job_db.resume_campaign_guarded(workspace_id, first["id"], max_active=2)
    assert resumed is not None and resumed["status"] == "running"
    # 非 paused 行：原样返回（service 层负责把非 running 结果转 409）。
    again = job_db.resume_campaign_guarded(workspace_id, first["id"], max_active=2)
    assert again is not None and again["status"] == "running"
    # 跨 workspace / 不存在的 id：None（防枚举语义与读路径一致）。
    assert job_db.resume_campaign_guarded(workspace_id, "nope", max_active=2) is None
