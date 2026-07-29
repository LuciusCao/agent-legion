from __future__ import annotations

from datetime import UTC, datetime, timedelta

from server.app.db.transaction import write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def test_metrics_overview_empty_response_shape(client) -> None:
    response = client.get("/api/metrics/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["granularity"] == "6h"
    assert isinstance(body["buckets"], list)


def test_metrics_overview_returns_inserted_buckets(client) -> None:
    # Use a bucket older than the sampler's last-completed-minute target so the
    # background ops-metrics loop cannot upsert over the inserted row.
    bucket = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=5)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into ops_metric_samples(
              bucket_start, online_workers, active_executions,
              input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (?, 2, 1, 10, 5, 1, 16)
            """,
            (bucket,),
        )
    response = client.get("/api/metrics/overview?granularity=6h")
    assert response.status_code == 200
    body = response.json()
    # The app's background sampler keeps writing its own minute rows into the
    # shared window, so locate the inserted bucket instead of counting rows.
    rows = [r for r in body["buckets"] if r["bucket_start"] == bucket.isoformat()]
    assert len(rows) == 1
    row = rows[0]
    assert row["online_workers"] == 2
    assert row["online_workers_max"] == 2
    assert row["active_executions"] == 1
    assert row["total_tokens"] == 16


def test_metrics_overview_rejects_invalid_granularity(client) -> None:
    response = client.get("/api/metrics/overview?granularity=week")
    assert response.status_code == 422


def test_metrics_overview_passes_worker_id_filter(client) -> None:
    # Same background-sampler guard as above: insert an old bucket, and the
    # sampler's own global rows are excluded by the worker_id filter anyway.
    bucket = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=5)
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            """
            insert into ops_metric_samples(
              bucket_start, worker_id, online_workers, active_executions,
              input_tokens, output_tokens, cache_read_tokens, total_tokens
            ) values (?, 'w-1', 1, 1, 10, 5, 1, 16)
            """,
            (bucket,),
        )
    response = client.get("/api/metrics/overview?granularity=6h&worker_id=w-1")
    assert response.status_code == 200
    rows = [r for r in response.json()["buckets"] if r["bucket_start"] == bucket.isoformat()]
    assert len(rows) == 1
    assert rows[0]["online_workers"] == 1
    assert rows[0]["total_tokens"] == 16


def test_metrics_overview_accepts_all_windows(client) -> None:
    for granularity in ("6h", "24h", "30d"):
        response = client.get(f"/api/metrics/overview?granularity={granularity}")
        assert response.status_code == 200
        assert response.json()["granularity"] == granularity
