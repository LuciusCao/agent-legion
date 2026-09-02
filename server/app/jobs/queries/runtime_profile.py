"""Runtime-profile gauge persistence on the JobQueries facade (BOUNDARY-DATA-001, #359).

The ops-metrics service (``services/ops_metrics/service.py``) and the
runtime-profile package call these facade methods for the
``ops_runtime_profile_samples`` table; the raw SQL lives here with the rest
of the queries layer. Upsert semantics mirror ``ops_metric_samples``: a
sampler restart inside the same minute overwrites the partial bucket instead
of double-counting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.app.db.dialect import ConnectSource, resolve_dsn
from server.app.jobs.queries.connection import ConnectionQueriesMixin


class RuntimeProfileQueriesMixin(ConnectionQueriesMixin):
    def upsert_runtime_profile_sample(self, bucket_start: datetime, values: dict[str, Any]) -> None:
        """Upsert one global gauge row for ``bucket_start`` (#359 L1).

        ``values`` carries the full gauge column set keyed by column name;
        the caller (the ops-metrics sampler) merges process-counter deltas
        with the depth gauges before calling.
        """
        columns = [
            "intake_runs",
            "intake_items",
            "pass_count",
            "pass_seconds_total",
            "pass_scan_seconds_max",
            "pass_slow_count",
            "enqueue_submitted",
            "enqueue_pool_skipped",
            "enqueue_pending",
            "enqueue_stock_gated",
            "claim_count",
            "claim_empty_count",
            "claim_seconds_total",
            "claim_seconds_max",
            "execute_active",
            "execute_done",
            "execute_requeued",
            "result_count",
            "result_seconds_total",
            "result_seconds_max",
            "db_pool_waiting",
            "db_pool_wait_seconds_total",
        ]
        placeholders = ", ".join(["%s"] * (len(columns) + 1))
        column_list = ", ".join(["bucket_start", *columns])
        update_list = ", ".join(f"{column} = excluded.{column}" for column in columns)
        params: tuple[Any, ...] = (bucket_start,)
        for column in columns:
            params = (*params, values[column])
        with self.write() as conn:
            conn.execute(
                f"insert into ops_runtime_profile_samples({column_list})"
                f" values ({placeholders})"
                f" on conflict (bucket_start) do update set {update_list}",
                params,
            )

    def recent_runtime_profile_samples(self, buckets: int = 30) -> list[dict[str, Any]]:
        """Read the most recent ``buckets`` gauge rows, oldest first."""
        with self.read() as conn:
            rows = conn.execute(
                "select * from ops_runtime_profile_samples order by bucket_start desc limit %s",
                (buckets,),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def delete_runtime_profile_samples_before(self, cutoff: datetime) -> int:
        """Retention cleanup for the profile gauge rows (#359).

        Shares the ops-metrics ``monitoring.retention_days`` window; called
        from the same cleanup pass as ``ops_metric_samples``.
        """
        with self.write() as conn:
            result = conn.execute(
                "delete from ops_runtime_profile_samples where bucket_start < %s",
                (cutoff,),
            )
            return result.rowcount


def runtime_profile_queries_from_dsn(dsn: ConnectSource) -> RuntimeProfileQueriesMixin:
    """Bare-DSN adapter for the profile mixin (#187 ConnectSource).

    The ops-metrics sampler and the profile route hold a plain DSN (or the
    JobQueries facade itself); constructing a full JobQueries would trigger
    init_db and needs a jobs_dir, neither of which a DSN-only holder must
    trigger. Mirrors ``global_settings_kv_from_dsn``.
    """
    queries = RuntimeProfileQueriesMixin.__new__(RuntimeProfileQueriesMixin)
    queries._path = resolve_dsn(dsn)  # data-layer-private field (see queries/base.py)
    return queries
