"""Schema v38: monitoring hot-path indexes.

``idx_node_run_token_usage_created_at`` serves the minute sampler's three
created_at-range aggregates (global, per-worker, per-workspace);
``idx_node_runs_status_finished_at_id`` serves the cleanup sweep's
``(finished_at, id)`` keyset pagination and replaces the old two-column
index as a covering leftmost prefix.
"""

from __future__ import annotations

from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def _index_names() -> set[str]:
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select indexname from pg_indexes where schemaname=current_schema()"
        ).fetchall()
    return {row["indexname"] for row in rows}


def test_token_usage_created_at_index_exists() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    assert "idx_node_run_token_usage_created_at" in _index_names()


def test_node_runs_composite_index_replaces_two_column() -> None:
    names = _index_names()
    assert "idx_node_runs_status_finished_at_id" in names
    assert "idx_node_runs_status_finished_at" not in names


def test_sweep_keyset_query_plans_on_composite_index() -> None:
    """The sweep's keyset pagination must be index-served, not a seq scan."""
    with read_connection(TEST_DATABASE_URL) as conn:
        conn.execute("set enable_seqscan=off")
        rows = conn.execute(
            """
            explain select id, job_id, node_key, log_path, run_dir, finished_at
            from node_runs
            where status = 'completed'
              and finished_at is not null
              and finished_at < now()
              and (finished_at > '2020-01-01'::timestamptz
                   or (finished_at = '2020-01-01'::timestamptz and id > 0))
            order by finished_at, id
            limit 500
            """
        ).fetchall()
    plan = "\n".join(str(row["QUERY PLAN"]) for row in rows)
    assert "idx_node_runs_status_finished_at_id" in plan
