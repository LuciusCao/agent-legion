from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server.app.db.transaction import read_connection, write_transaction
from server.app.services.ops_metrics import OpsMetricsService
from tests.helpers.seed import insert_token_usage
from tests.postgres_support import TEST_DATABASE_URL

_NOW = datetime(2026, 7, 26, 12, 34, 45, tzinfo=UTC)


def _service(config: dict | None = None) -> OpsMetricsService:
    return OpsMetricsService(TEST_DATABASE_URL, config or {})


def _bucket_start(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0) - timedelta(minutes=1)


def _fetch_sample(
    bucket_start: datetime, worker_id: str = "", workspace_id: str = ""
) -> dict | None:
    with read_connection(TEST_DATABASE_URL) as conn:
        return conn.execute(
            "select * from ops_metric_samples"
            " where bucket_start = %s and worker_id = %s and workspace_id = %s",
            (bucket_start, worker_id, workspace_id),
        ).fetchone()


def _seed_workspace_job(job_id: str = "job-1", workspace_id: str = "ops-ws") -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values (%s, 'Ops', 'demo_workflow') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, %s, 'questions', 'question', %s) on conflict(id) do nothing",
            (job_id, workspace_id, job_id),
        )


def _insert_token_usage(
    run_id: int,
    created_at: datetime,
    *,
    job_id: str = "job-1",
    workspace_id: str = "ops-ws",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read_tokens: int = 1,
) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into node_runs(id, job_id, node_key, status)"
            " values (%s, %s, 'generate', 'completed')",
            (run_id, job_id),
        )
        insert_token_usage(
            conn,
            node_run_id=run_id,
            job_id=job_id,
            workspace_id=workspace_id,
            node_key="generate",
            provider="p",
            model="m",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            created_at=created_at,
        )


def _insert_worker(worker_id: str, last_seen_at: datetime, *, revoked: bool = False) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into agent_workers(
              worker_id, name, runtimes_json, max_concurrency, protocol_version,
              token_hash, registered_at, last_seen_at, revoked_at
            ) values (%s, 'w', '["pi"]', 1, 1, 'hash', %s, %s, %s)
            """,
            (worker_id, last_seen_at, last_seen_at, last_seen_at if revoked else None),
        )


def _insert_execution(
    execution_id: str,
    state: str,
    *,
    worker_id: str | None = None,
    node_run_id: int | None = None,
    queued_at: datetime = _NOW,
) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into agent_execution_requests(
              execution_id, workspace_id, job_id, workflow_key, node_key,
              agent_id, agent_definition_hash, node_concurrency_limit, state,
              worker_id, node_run_id, queued_at, manifest_json
            ) values (%s, 'ops-ws', 'job-1', 'questions', %s, 'agent-1', 'hash', 5, %s, %s, %s, %s, '{}')
            """,
            (execution_id, f"node-{execution_id}", state, worker_id, node_run_id, queued_at),
        )


def _insert_sample(
    bucket_start: datetime,
    *,
    worker_id: str = "",
    online_workers: int = 0,
    active_executions: int = 0,
    queued: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into ops_metric_samples(
              bucket_start, worker_id, online_workers, active_executions,
              queued, input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                bucket_start,
                worker_id,
                online_workers,
                active_executions,
                queued,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                total_tokens,
            ),
        )


def test_sample_once_writes_previous_minute_bucket() -> None:
    _service().sample_once(_NOW)
    row = _fetch_sample(_bucket_start(_NOW))
    assert row is not None
    assert row["online_workers"] == 0
    assert row["active_executions"] == 0
    assert row["total_tokens"] == 0


def test_sample_once_upserts_existing_bucket() -> None:
    service = _service()
    service.sample_once(_NOW)
    _insert_worker("w-1", _NOW)
    service.sample_once(_NOW)
    row = _fetch_sample(_bucket_start(_NOW))
    assert row is not None
    assert row["online_workers"] == 1
    with read_connection(TEST_DATABASE_URL) as conn:
        count = conn.execute(
            "select count(*) as c from ops_metric_samples"
            " where bucket_start = %s and worker_id = ''",
            (_bucket_start(_NOW),),
        ).fetchone()["c"]
    assert count == 1


def test_sample_counts_only_tokens_inside_bucket() -> None:
    _seed_workspace_job()
    bucket = _bucket_start(_NOW)
    _insert_token_usage(1, bucket + timedelta(seconds=30))  # inside bucket
    _insert_token_usage(2, bucket - timedelta(seconds=1))  # before bucket
    _insert_token_usage(3, bucket + timedelta(minutes=1))  # at bucket end (next bucket)
    _service().sample_once(_NOW)
    row = _fetch_sample(bucket)
    assert row is not None
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 5
    assert row["cache_read_tokens"] == 1
    assert row["total_tokens"] == 16


def test_sample_counts_online_workers_within_threshold() -> None:
    _insert_worker("online", _NOW)
    _insert_worker("stale", _NOW - timedelta(seconds=60))
    _insert_worker("revoked", _NOW, revoked=True)
    _service().sample_once(_NOW)
    row = _fetch_sample(_bucket_start(_NOW))
    assert row is not None
    assert row["online_workers"] == 1


def test_sample_counts_only_claimed_executions() -> None:
    _seed_workspace_job()
    _insert_execution("e-queued", "queued")
    _insert_execution("e-claimed", "claimed")
    _insert_execution("e-done", "done")
    _service().sample_once(_NOW)
    row = _fetch_sample(_bucket_start(_NOW))
    assert row is not None
    assert row["active_executions"] == 1


def test_sample_writes_per_worker_rows_with_attributed_tokens() -> None:
    _seed_workspace_job()
    bucket = _bucket_start(_NOW)
    _insert_worker("w-a", _NOW)
    _insert_worker("w-b", _NOW)
    _insert_execution("e-a", "claimed", worker_id="w-a", node_run_id=11)
    _insert_execution("e-b", "claimed", worker_id="w-b", node_run_id=12)
    _insert_token_usage(
        11, bucket + timedelta(seconds=10), input_tokens=100, output_tokens=50, cache_read_tokens=10
    )
    _insert_token_usage(
        12,
        bucket + timedelta(seconds=20),
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=20,
    )
    # Host-local run: no agent_execution_requests row, counted only globally.
    _insert_token_usage(
        13, bucket + timedelta(seconds=30), input_tokens=7, output_tokens=3, cache_read_tokens=1
    )
    _service().sample_once(_NOW)

    global_row = _fetch_sample(bucket)
    assert global_row is not None
    assert global_row["online_workers"] == 2
    assert global_row["active_executions"] == 2
    assert global_row["total_tokens"] == 160 + 320 + 11

    row_a = _fetch_sample(bucket, "w-a")
    assert row_a is not None
    assert row_a["online_workers"] == 1
    assert row_a["active_executions"] == 1
    assert row_a["input_tokens"] == 100
    assert row_a["output_tokens"] == 50
    assert row_a["cache_read_tokens"] == 10
    assert row_a["total_tokens"] == 160

    row_b = _fetch_sample(bucket, "w-b")
    assert row_b is not None
    assert row_b["online_workers"] == 1
    assert row_b["active_executions"] == 1
    assert row_b["total_tokens"] == 320

    assert row_a["total_tokens"] + row_b["total_tokens"] < global_row["total_tokens"]


def test_sample_once_upserts_per_worker_rows_idempotently() -> None:
    _seed_workspace_job()
    service = _service()
    _insert_worker("w-1", _NOW)
    service.sample_once(_NOW)
    _insert_execution("e-1", "claimed", worker_id="w-1")
    service.sample_once(_NOW)
    bucket = _bucket_start(_NOW)
    row = _fetch_sample(bucket, "w-1")
    assert row is not None
    assert row["online_workers"] == 1
    assert row["active_executions"] == 1
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select worker_id, workspace_id from ops_metric_samples"
            " where bucket_start = %s order by worker_id, workspace_id",
            (bucket,),
        ).fetchall()
    # 全局行 + per-worker 行之外，claimed 执行还产生 per-workspace 行（v23）。
    assert [(r["worker_id"], r["workspace_id"]) for r in rows] == [
        ("", ""),
        ("", "ops-ws"),
        ("w-1", ""),
    ]


def test_query_series_6h_returns_raw_minute_rows_in_order() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _insert_sample(now - timedelta(minutes=3), online_workers=2, total_tokens=30)
    _insert_sample(now - timedelta(minutes=1), online_workers=1, total_tokens=10)
    rows = _service().query_series("6h")
    assert [r["total_tokens"] for r in rows] == [30, 10]
    assert rows[0]["online_workers"] == 2
    assert rows[0]["online_workers_max"] == 2
    assert rows[0]["active_executions_max"] == rows[0]["active_executions"]
    # ISO-8601 with timezone offset, parseable back to the stored instant.
    assert datetime.fromisoformat(rows[0]["bucket_start"]) == now - timedelta(minutes=3)


def test_query_series_6h_window_excludes_old_rows() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _insert_sample(now - timedelta(hours=7), total_tokens=99)
    _insert_sample(now - timedelta(minutes=30), total_tokens=1)
    rows = _service().query_series("6h")
    assert [r["total_tokens"] for r in rows] == [1]


def test_query_series_24h_rolls_up_into_5min_bins() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    bin_start = now - timedelta(minutes=now.minute % 5)
    # 同一 5 分钟桶内的两个采样（如 10:01 与 10:04）聚合为一行
    _insert_sample(
        bin_start + timedelta(minutes=1),
        online_workers=2,
        active_executions=1,
        input_tokens=10,
        total_tokens=20,
    )
    _insert_sample(
        bin_start + timedelta(minutes=4),
        online_workers=4,
        active_executions=3,
        input_tokens=30,
        total_tokens=60,
    )
    rows = _service().query_series("24h")
    assert len(rows) == 1
    row = rows[0]
    assert datetime.fromisoformat(row["bucket_start"]) == bin_start
    assert row["online_workers"] == 3
    assert row["online_workers_max"] == 4
    assert row["active_executions"] == 2
    assert row["active_executions_max"] == 3
    assert row["input_tokens"] == 40
    assert row["total_tokens"] == 80


def test_query_series_24h_bin_boundary_splits_adjacent_bins() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    bin_start = now - timedelta(minutes=now.minute % 5)
    _insert_sample(bin_start - timedelta(minutes=1), total_tokens=10)  # 上一桶
    _insert_sample(bin_start, total_tokens=20)  # 当前桶起点
    rows = _service().query_series("24h")
    assert [r["total_tokens"] for r in rows] == [10, 20]
    assert datetime.fromisoformat(rows[0]["bucket_start"]) == bin_start - timedelta(minutes=5)
    assert datetime.fromisoformat(rows[1]["bucket_start"]) == bin_start


def test_query_series_30d_rolls_up_into_4hour_bins() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    # 4 小时桶边界对齐 00/04/08/12/16/20 UTC：05:23 落入 04:00 桶
    day_start = now.replace(hour=0, minute=0)
    _insert_sample(day_start + timedelta(hours=5, minutes=23), online_workers=1, total_tokens=5)
    _insert_sample(day_start + timedelta(hours=6), online_workers=5, total_tokens=15)
    _insert_sample(day_start - timedelta(days=31), total_tokens=999)
    rows = _service().query_series("30d")
    assert len(rows) == 1
    row = rows[0]
    assert datetime.fromisoformat(row["bucket_start"]) == day_start + timedelta(hours=4)
    assert row["online_workers"] == 3
    assert row["online_workers_max"] == 5
    assert row["total_tokens"] == 20


def test_query_series_filters_by_worker_id() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=5)
    _insert_sample(now, online_workers=2, total_tokens=35)
    _insert_sample(now, worker_id="w-1", online_workers=1, total_tokens=10)
    _insert_sample(now, worker_id="w-2", online_workers=1, total_tokens=20)
    global_rows = _service().query_series("6h")
    assert [r["total_tokens"] for r in global_rows] == [35]
    assert [r["online_workers"] for r in global_rows] == [2]
    worker_rows = _service().query_series("6h", worker_id="w-1")
    assert [r["total_tokens"] for r in worker_rows] == [10]
    assert worker_rows[0]["online_workers"] == 1
    assert _service().query_series("6h", worker_id="w-unknown") == []


def test_query_series_24h_rolls_up_per_worker_rows() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    bin_start = now - timedelta(minutes=now.minute % 5)
    _insert_sample(
        bin_start + timedelta(minutes=1),
        worker_id="w-1",
        online_workers=1,
        active_executions=1,
        input_tokens=10,
        total_tokens=20,
    )
    _insert_sample(
        bin_start + timedelta(minutes=2),
        worker_id="w-1",
        online_workers=0,
        active_executions=3,
        input_tokens=30,
        total_tokens=60,
    )
    # Other scopes in the same bin must not leak into the w-1 rollup.
    _insert_sample(bin_start + timedelta(minutes=1), worker_id="w-2", total_tokens=999)
    _insert_sample(bin_start + timedelta(minutes=1), total_tokens=999)
    rows = _service().query_series("24h", worker_id="w-1")
    assert len(rows) == 1
    row = rows[0]
    assert datetime.fromisoformat(row["bucket_start"]) == bin_start
    assert row["online_workers"] == 1
    assert row["online_workers_max"] == 1
    assert row["active_executions"] == 2
    assert row["active_executions_max"] == 3
    assert row["input_tokens"] == 40
    assert row["total_tokens"] == 80


def test_cleanup_expired_deletes_only_rows_past_retention() -> None:
    now = datetime(2026, 7, 26, tzinfo=UTC)
    _insert_sample(now - timedelta(days=8), total_tokens=1)
    _insert_sample(now - timedelta(days=6), total_tokens=2)
    service = _service({"monitoring": {"retention_days": 7}})
    deleted = service.cleanup_expired(now)
    assert deleted == 1
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute("select total_tokens from ops_metric_samples").fetchall()
    assert [row["total_tokens"] for row in rows] == [2]


def test_service_reads_monitoring_config_defaults() -> None:
    service = _service()
    assert service.sample_interval_seconds == 60
    custom = _service({"monitoring": {"sample_interval_seconds": 15, "retention_days": 3}})
    assert custom.sample_interval_seconds == 15


def test_sample_catch_up_backfills_missing_minutes() -> None:
    service = _service()
    service.sample_once(_NOW)
    written = service.sample_catch_up(_NOW + timedelta(minutes=3))
    assert written == 3
    for offset in range(1, 4):
        row = _fetch_sample(_bucket_start(_NOW) + timedelta(minutes=offset))
        assert row is not None, f"missing bucket +{offset}min"


def test_sample_catch_up_is_idempotent() -> None:
    service = _service()
    first = service.sample_catch_up(_NOW)
    assert first == 10  # empty table: only the capped horizon is written
    assert service.sample_catch_up(_NOW) == 0


def test_sample_catch_up_caps_backfill_horizon() -> None:
    service = _service()
    service.sample_once(_NOW)
    written = service.sample_catch_up(_NOW + timedelta(minutes=60))
    assert written == 10
    # Buckets older than the horizon stay empty: no fabricated history.
    assert _fetch_sample(_bucket_start(_NOW) + timedelta(minutes=1)) is None


def _insert_node_run(
    run_id: int,
    status: str,
    started_at: datetime,
    finished_at: datetime | None,
    *,
    job_id: str = "job-1",
    agent_run: bool = True,
) -> None:
    # agent_run=True 时补一行 agent_execution_requests：summary 的 Agent runs
    # 口径只统计有执行请求引用的 run（Host 本地 handler 代码节点没有）。
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into node_runs(id, job_id, node_key, status, started_at, finished_at)"
            " values (%s, %s, 'generate', %s, %s, %s)",
            (run_id, job_id, status, started_at, finished_at),
        )
        if agent_run:
            conn.execute(
                """
                insert into agent_execution_requests(
                    execution_id, workspace_id, job_id, workflow_key, node_key,
                    agent_id, agent_definition_hash, node_concurrency_limit,
                    state, queued_at, node_run_id, manifest_json
                )
                values (%s, 'ops-ws', %s, 'questions', 'generate', 'agent-1', 'hash', 1,
                        'done', %s, %s, '{}')
                """,
                (f"exec-{run_id}", job_id, started_at, run_id),
            )


def test_query_summary_aggregates_recent_hour_independent_of_window() -> None:
    _seed_workspace_job()
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    # 近 1 小时内的两个分钟样本 + 一个超出窗口的旧样本（不计入 token 求和）。
    _insert_sample(now - timedelta(minutes=3), online_workers=1, input_tokens=10, total_tokens=20)
    _insert_sample(
        now - timedelta(minutes=1),
        online_workers=2,
        active_executions=1,
        input_tokens=30,
        output_tokens=5,
        cache_read_tokens=2,
        total_tokens=60,
    )
    _insert_sample(now - timedelta(minutes=90), total_tokens=999)
    # 完成的 runs：10s / 20s / 30s -> p50=20, p95=29；failed 不计入分位数。
    for run_id, seconds in ((1, 10), (2, 20), (3, 30)):
        _insert_node_run(
            run_id,
            "completed",
            now - timedelta(minutes=30, seconds=seconds),
            now - timedelta(minutes=30),
        )
    _insert_node_run(4, "failed", now - timedelta(seconds=700), now - timedelta(seconds=600))
    # 超出 1 小时窗口的终态 run 与仍在运行的 run 都不计入。
    _insert_node_run(5, "completed", now - timedelta(hours=3), now - timedelta(hours=2))
    _insert_node_run(6, "running", now - timedelta(minutes=5), None)

    summary = _service().query_summary()
    assert summary["online_workers"] == 2
    assert summary["active_executions"] == 1
    tokens = summary["recent_hour_tokens"]
    assert tokens["input_tokens"] == 40
    assert tokens["output_tokens"] == 5
    assert tokens["cache_read_tokens"] == 2
    assert tokens["total_tokens"] == 80
    runs = summary["recent_hour_runs"]
    assert runs["completed"] == 3
    assert runs["failed"] == 1
    assert runs["duration_p50_seconds"] == 20.0
    assert runs["duration_p95_seconds"] == 29.0


def test_query_summary_empty_tables_return_zeroes_and_nulls() -> None:
    summary = _service().query_summary()
    assert summary["online_workers"] is None
    assert summary["active_executions"] is None
    assert summary["recent_hour_tokens"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": 0,
    }
    assert summary["recent_hour_runs"] == {
        "completed": 0,
        "failed": 0,
        "duration_p50_seconds": None,
        "duration_p95_seconds": None,
    }


def test_query_summary_scopes_tokens_and_gauges_by_worker() -> None:
    _seed_workspace_job()
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _insert_sample(now - timedelta(minutes=1), online_workers=2, total_tokens=100)
    _insert_sample(now - timedelta(minutes=1), worker_id="w-1", online_workers=1, total_tokens=40)
    _insert_node_run(1, "completed", now - timedelta(seconds=20), now - timedelta(seconds=10))

    scoped = _service().query_summary(worker_id="w-1")
    assert scoped["online_workers"] == 1
    assert scoped["recent_hour_tokens"]["total_tokens"] == 40
    # node_runs 无 worker 归属，runs 统计始终全局。
    assert scoped["recent_hour_runs"]["completed"] == 1
    assert _service().query_summary(worker_id="w-unknown")["online_workers"] is None


def test_query_summary_excludes_host_local_handler_runs() -> None:
    _seed_workspace_job()
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    # Agent run（有 agent_execution_requests 引用）计入；
    # Host 本地 handler 代码节点 run（无引用）不计入，也不影响耗时分位数。
    _insert_node_run(1, "completed", now - timedelta(minutes=5), now - timedelta(minutes=4))
    _insert_node_run(
        2, "completed", now - timedelta(minutes=5), now - timedelta(minutes=4), agent_run=False
    )
    _insert_node_run(
        3, "failed", now - timedelta(minutes=3), now - timedelta(minutes=2), agent_run=False
    )

    runs = _service().query_summary()["recent_hour_runs"]
    assert runs["completed"] == 1
    assert runs["failed"] == 0
    assert runs["duration_p50_seconds"] == 60.0
    assert runs["duration_p95_seconds"] == 60.0


def test_sample_once_records_queue_depth_on_global_row() -> None:
    _seed_workspace_job()
    _insert_execution("ex-q1", "queued")
    _insert_execution("ex-q2", "queued")
    _insert_execution("ex-c1", "claimed")

    _service().sample_once(_NOW)

    row = _fetch_sample(_bucket_start(_NOW))
    assert row is not None
    assert row["queued"] == 2
    assert row["active_executions"] == 1


def test_query_series_includes_queued_fields() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _insert_sample(now - timedelta(minutes=1), queued=123, online_workers=1)

    rows = _service().query_series("6h")

    assert len(rows) == 1
    assert rows[0]["queued"] == 123
    assert rows[0]["queued_max"] == 123


def test_query_summary_reports_queue_depth_oldest_and_sweeper_count() -> None:
    _seed_workspace_job()
    oldest = datetime.now(UTC) - timedelta(hours=3)
    _insert_execution("ex-old", "queued", queued_at=oldest)
    _insert_execution("ex-new", "queued", queued_at=datetime.now(UTC))
    with write_transaction(TEST_DATABASE_URL) as conn:
        for index in range(2):
            conn.execute(
                "insert into job_nodes(job_id, node_key, status, failure_detail, finished_at)"
                " values ('job-1', %s, 'failed', 'unclaimable_model', current_timestamp)",
                (f"poison-{index}",),
            )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status, failure_detail, finished_at)"
            " values ('job-1', 'old-poison', 'failed', 'unclaimable_model', %s)",
            (datetime.now(UTC) - timedelta(hours=2),),
        )

    summary = _service().query_summary()

    assert summary["queue"]["queued"] == 2
    assert summary["queue"]["oldest_queued_at"] is not None
    assert summary["queue"]["recent_hour_unclaimable_failed"] == 2
    assert summary["queue_alert"] is None or summary["queue_alert"]["kind"] == "stalled"


def test_queue_alert_blocked_from_fresh_signal() -> None:
    _seed_workspace_job()
    _insert_execution("ex-blocked", "queued")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into agent_queue_signals(id, kind, reasons_json, updated_at)"
            " values (1, 'blocked', '{\"capability_or_model_mismatch\": 8}', current_timestamp)"
        )

    alert = _service().query_summary()["queue_alert"]

    assert alert is not None
    assert alert["kind"] == "blocked"
    assert alert["reasons"] == {"capability_or_model_mismatch": 8}
    assert alert["at"] is not None


def test_queue_alert_ignores_stale_signal() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into agent_queue_signals(id, kind, reasons_json, updated_at)"
            " values (1, 'blocked', '{}', %s)",
            (datetime.now(UTC) - timedelta(minutes=30),),
        )

    assert _service().query_summary()["queue_alert"] is None


def test_queue_alert_stalled_when_queue_idle_with_online_workers() -> None:
    _seed_workspace_job()
    _insert_execution("ex-stuck", "queued", queued_at=datetime.now(UTC) - timedelta(minutes=5))
    _insert_sample(_bucket_start(datetime.now(UTC)), online_workers=2, active_executions=0)

    alert = _service().query_summary()["queue_alert"]

    assert alert is not None
    assert alert["kind"] == "stalled"


def test_queue_alert_none_when_executions_running_or_no_workers() -> None:
    _seed_workspace_job()
    _insert_execution("ex-waiting", "queued", queued_at=datetime.now(UTC) - timedelta(minutes=5))
    # Worker 忙满（有执行在跑）不算停滞。
    _insert_sample(_bucket_start(datetime.now(UTC)), online_workers=2, active_executions=3)
    assert _service().query_summary()["queue_alert"] is None

    # 没有在线 worker 时不误报（部署缺口由在线 Worker 卡片呈现）。
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute("delete from ops_metric_samples")
    _insert_sample(_bucket_start(datetime.now(UTC)), online_workers=0, active_executions=0)
    assert _service().query_summary()["queue_alert"] is None


def test_blocked_signal_written_by_empty_claim_diagnostics() -> None:
    import json as _json

    from server.app.agent_broker.empty_diagnostics import log_blocked_queue

    _seed_workspace_job()
    _insert_execution("ex-head", "queued")
    with read_connection(TEST_DATABASE_URL) as conn:
        log_blocked_queue(TEST_DATABASE_URL, conn, {"capability_or_model_mismatch": 3})

    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select kind, reasons_json from agent_queue_signals where id=1"
        ).fetchone()
    assert row is not None
    assert row["kind"] == "blocked"
    assert _json.loads(row["reasons_json"]) == {"capability_or_model_mismatch": 3}


def test_sample_writes_per_workspace_rows() -> None:
    _seed_workspace_job()
    _insert_execution("e-q1", "queued")
    _insert_execution("e-c1", "claimed", worker_id="w-1")

    _service().sample_once(_NOW)

    row = _fetch_sample(_bucket_start(_NOW), workspace_id="ops-ws")
    assert row is not None
    assert row["queued"] == 1
    assert row["active_executions"] == 1
    assert row["online_workers"] == 0
    assert row["total_tokens"] == 0


def test_query_series_scopes_to_workspace_rows() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    bucket = now - timedelta(minutes=1)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into ops_metric_samples(bucket_start, worker_id, workspace_id, queued)"
            " values (%s, '', 'ops-ws', 7), (%s, '', '', 99)",
            (bucket, bucket),
        )

    ws_rows = _service().query_series("6h", workspace_id="ops-ws")
    global_rows = _service().query_series("6h")

    assert [r["queued"] for r in ws_rows] == [7]
    assert [r["queued"] for r in global_rows] == [99]


def _insert_execution_ws(
    execution_id: str, state: str, *, workspace_id: str, job_id: str, queued_at: datetime
) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into agent_execution_requests(
              execution_id, workspace_id, job_id, workflow_key, node_key,
              agent_id, agent_definition_hash, node_concurrency_limit, state,
              queued_at, manifest_json
            ) values (%s, %s, %s, 'questions', %s, 'agent-1', 'hash', 5, %s, %s, '{}')
            """,
            (execution_id, workspace_id, job_id, f"node-{execution_id}", state, queued_at),
        )


def test_query_summary_scopes_queue_to_workspace() -> None:
    _seed_workspace_job()
    _seed_workspace_job(job_id="job-2", workspace_id="ops-ws-2")
    _insert_execution("e-a", "queued")
    _insert_execution_ws("e-b", "queued", workspace_id="ops-ws-2", job_id="job-2", queued_at=_NOW)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into job_nodes(job_id, node_key, status, failure_detail, finished_at)"
            " values ('job-2', 'poison-b', 'failed', 'unclaimable_model', current_timestamp)"
        )

    summary = _service().query_summary(workspace_id="ops-ws-2")

    assert summary["queue"]["queued"] == 1
    assert summary["queue"]["recent_hour_unclaimable_failed"] == 1


def test_queue_alert_stalled_scopes_to_workspace() -> None:
    _seed_workspace_job()
    _seed_workspace_job(job_id="job-2", workspace_id="ops-ws-2")
    _insert_execution("e-a", "queued", queued_at=datetime.now(UTC) - timedelta(minutes=5))
    # 全局有在线 worker；ops-ws-2 无 queued，不应报 stalled。
    with write_transaction(TEST_DATABASE_URL) as conn:
        bucket = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
        conn.execute(
            "insert into ops_metric_samples(bucket_start, worker_id, workspace_id,"
            " online_workers, active_executions) values (%s, '', '', 2, 0),"
            " (%s, '', 'ops-ws', 0, 0)",
            (bucket, bucket),
        )

    assert _service().query_summary(workspace_id="ops-ws")["queue_alert"]["kind"] == "stalled"
    assert _service().query_summary(workspace_id="ops-ws-2")["queue_alert"] is None


def test_queue_alert_blocked_requires_workspace_queue() -> None:
    _seed_workspace_job()
    _insert_execution("e-a", "queued")
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into agent_queue_signals(id, kind, reasons_json, updated_at)"
            " values (1, 'blocked', '{\"capability_or_model_mismatch\": 4}', current_timestamp)"
        )

    assert _service().query_summary(workspace_id="ops-ws")["queue_alert"]["kind"] == "blocked"
    # 该 workspace 没有 queued 行：fleet 级 blocked 信号不外溢到其他 workspace。
    assert _service().query_summary(workspace_id="ops-ws-2")["queue_alert"] is None


def test_query_summary_serves_cached_result_within_ttl() -> None:
    """UI 轮询在 TTL 内命中缓存，不重复扫库。"""
    service = _service()
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _insert_sample(now - timedelta(minutes=1), online_workers=1)

    first = service.query_summary()
    assert first["online_workers"] == 1

    _insert_sample(now, online_workers=2)
    second = service.query_summary()
    assert second["online_workers"] == 1


def test_query_summary_cache_expires_after_ttl(monkeypatch) -> None:
    monkeypatch.setattr("server.app.services.ops_metrics.summary._SUMMARY_CACHE_TTL_SECONDS", 0)
    service = _service()
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _insert_sample(now - timedelta(minutes=1), online_workers=1)

    first = service.query_summary()
    assert first["online_workers"] == 1

    _insert_sample(now, online_workers=2)
    second = service.query_summary()
    assert second["online_workers"] == 2


def test_query_summary_cache_is_keyed_per_scope() -> None:
    """worker 维度的缓存不得串到全局维度（反之亦然）。"""
    service = _service()
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _insert_sample(now - timedelta(minutes=1), online_workers=2)
    _insert_sample(now - timedelta(minutes=1), worker_id="w-1", online_workers=1)

    assert service.query_summary()["online_workers"] == 2
    assert service.query_summary(worker_id="w-1")["online_workers"] == 1
    assert service.query_summary()["online_workers"] == 2
