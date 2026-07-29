from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server.app.db.transaction import read_connection, write_transaction
from server.app.services.ops_metrics import OpsMetricsService
from tests.postgres_support import TEST_DATABASE_URL

_NOW = datetime(2026, 7, 26, 12, 34, 45, tzinfo=UTC)


def _service(config: dict | None = None) -> OpsMetricsService:
    return OpsMetricsService(TEST_DATABASE_URL, config or {})


def _bucket_start(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0) - timedelta(minutes=1)


def _fetch_sample(bucket_start: datetime, worker_id: str = "") -> dict | None:
    with read_connection(TEST_DATABASE_URL) as conn:
        return conn.execute(
            "select * from ops_metric_samples where bucket_start = ? and worker_id = ?",
            (bucket_start, worker_id),
        ).fetchone()


def _seed_workspace_job(job_id: str = "job-1", workspace_id: str = "ops-ws") -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into workspaces(id, name) values (?, 'Ops') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (?, ?, 'questions', 'question', ?) on conflict(id) do nothing",
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
    total = input_tokens + output_tokens + cache_read_tokens
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "insert into node_runs(id, job_id, node_key, status)"
            " values (?, ?, 'generate', 'completed')",
            (run_id, job_id),
        )
        conn.execute(
            """
            insert into node_run_token_usage(
              node_run_id, job_id, workspace_id, node_key, provider, model,
              input_tokens, output_tokens, cache_read_tokens, total_tokens, created_at
            ) values (?, ?, ?, 'generate', 'p', 'm', ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                job_id,
                workspace_id,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                total,
                created_at,
            ),
        )


def _insert_worker(worker_id: str, last_seen_at: datetime, *, revoked: bool = False) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into agent_workers(
              worker_id, name, runtimes_json, max_concurrency, protocol_version,
              token_hash, registered_at, last_seen_at, revoked_at
            ) values (?, 'w', '["pi"]', 1, 1, 'hash', ?, ?, ?)
            """,
            (worker_id, last_seen_at, last_seen_at, last_seen_at if revoked else None),
        )


def _insert_execution(
    execution_id: str,
    state: str,
    *,
    worker_id: str | None = None,
    node_run_id: int | None = None,
) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into agent_execution_requests(
              execution_id, workspace_id, job_id, workflow_key, node_key,
              agent_id, agent_definition_hash, node_concurrency_limit, state,
              worker_id, node_run_id, queued_at, manifest_json
            ) values (?, 'ops-ws', 'job-1', 'questions', ?, 'agent-1', 'hash', 5, ?, ?, ?, ?, '{}')
            """,
            (execution_id, f"node-{execution_id}", state, worker_id, node_run_id, _NOW),
        )


def _insert_sample(
    bucket_start: datetime,
    *,
    worker_id: str = "",
    online_workers: int = 0,
    active_executions: int = 0,
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
              input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bucket_start,
                worker_id,
                online_workers,
                active_executions,
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
            " where bucket_start = ? and worker_id = ''",
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
            "select worker_id from ops_metric_samples where bucket_start = ? order by worker_id",
            (bucket,),
        ).fetchall()
    assert [r["worker_id"] for r in rows] == ["", "w-1"]


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
