"""Sampling persistence helpers for the Host operations metrics service."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.app.db.connection import DatabaseConnection

_EMPTY_TOKENS: dict[str, int] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "total_tokens": 0,
}


def _upsert_sample(
    conn: DatabaseConnection,
    bucket_start: datetime,
    worker_id: str,
    *,
    online_workers: int,
    active_executions: int,
    tokens: dict[str, Any],
    queued: int = 0,
) -> None:
    conn.execute(
        """
        insert into ops_metric_samples(
          bucket_start, worker_id, online_workers, active_executions,
          queued, input_tokens, output_tokens, cache_read_tokens, total_tokens
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (bucket_start, worker_id) do update set
          online_workers=excluded.online_workers,
          active_executions=excluded.active_executions,
          queued=excluded.queued,
          input_tokens=excluded.input_tokens,
          output_tokens=excluded.output_tokens,
          cache_read_tokens=excluded.cache_read_tokens,
          total_tokens=excluded.total_tokens
        """,
        (
            bucket_start,
            worker_id,
            online_workers,
            active_executions,
            queued,
            tokens["input_tokens"],
            tokens["output_tokens"],
            tokens["cache_read_tokens"],
            tokens["total_tokens"],
        ),
    )
