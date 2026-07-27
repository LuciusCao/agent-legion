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


def _fetch_sample(bucket_start: datetime) -> dict | None:
    with read_connection(TEST_DATABASE_URL) as conn:
        return conn.execute(
            "select * from ops_metric_samples where bucket_start = ?", (bucket_start,)
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


def _insert_execution(execution_id: str, state: str) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into agent_execution_requests(
              execution_id, workspace_id, job_id, workflow_key, node_key,
              agent_id, agent_definition_hash, node_concurrency_limit, state,
              queued_at, manifest_json
            ) values (?, 'ops-ws', 'job-1', 'questions', ?, 'agent-1', 'hash', 5, ?, ?, '{}')
            """,
            (execution_id, f"node-{execution_id}", state, _NOW),
        )


def _insert_sample(
    bucket_start: datetime,
    *,
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
              bucket_start, online_workers, active_executions,
              input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bucket_start,
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
            "select count(*) as c from ops_metric_samples where bucket_start = ?",
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


def test_query_series_minute_returns_raw_rows_in_order() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _insert_sample(now - timedelta(minutes=3), online_workers=2, total_tokens=30)
    _insert_sample(now - timedelta(minutes=1), online_workers=1, total_tokens=10)
    rows = _service().query_series("minute", hours=6, days=7)
    assert [r["total_tokens"] for r in rows] == [30, 10]
    assert rows[0]["online_workers"] == 2
    assert rows[0]["online_workers_max"] == 2
    assert rows[0]["active_executions_max"] == rows[0]["active_executions"]
    # ISO-8601 with timezone offset, parseable back to the stored instant.
    assert datetime.fromisoformat(rows[0]["bucket_start"]) == now - timedelta(minutes=3)


def test_query_series_minute_window_excludes_old_rows() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    _insert_sample(now - timedelta(hours=2), total_tokens=99)
    _insert_sample(now - timedelta(minutes=30), total_tokens=1)
    rows = _service().query_series("minute", hours=1, days=7)
    assert [r["total_tokens"] for r in rows] == [1]


def test_query_series_hour_rolls_up_sum_avg_max() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    hour_start = now.replace(minute=0)
    _insert_sample(
        hour_start + timedelta(minutes=1),
        online_workers=2,
        active_executions=1,
        input_tokens=10,
        total_tokens=20,
    )
    _insert_sample(
        hour_start + timedelta(minutes=2),
        online_workers=4,
        active_executions=3,
        input_tokens=30,
        total_tokens=60,
    )
    rows = _service().query_series("hour", hours=6, days=7)
    assert len(rows) == 1
    row = rows[0]
    assert datetime.fromisoformat(row["bucket_start"]) == hour_start
    assert row["online_workers"] == 3
    assert row["online_workers_max"] == 4
    assert row["active_executions"] == 2
    assert row["active_executions_max"] == 3
    assert row["input_tokens"] == 40
    assert row["total_tokens"] == 80


def test_query_series_day_rolls_up_by_day_window() -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    day_start = now.replace(hour=0, minute=0)
    _insert_sample(day_start + timedelta(hours=1), online_workers=1, total_tokens=5)
    _insert_sample(day_start + timedelta(hours=2), online_workers=5, total_tokens=15)
    _insert_sample(day_start - timedelta(days=10), total_tokens=999)
    rows = _service().query_series("day", hours=6, days=7)
    assert len(rows) == 1
    row = rows[0]
    assert datetime.fromisoformat(row["bucket_start"]) == day_start
    assert row["online_workers"] == 3
    assert row["online_workers_max"] == 5
    assert row["total_tokens"] == 20


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
