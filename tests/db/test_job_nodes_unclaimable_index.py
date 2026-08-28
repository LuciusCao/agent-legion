"""Schema v48: partial index for the unclaimable_model sweep counter (issue #106).

``ops_metrics.queue.query_queue_summary`` counts ``job_nodes`` rows with
``failure_detail='unclaimable_model'`` finished within the last hour, both
fleet-wide and per workspace. At prod scale (2.6M rows) the count seq-scanned
the whole table (40s+ per collection); ``idx_job_nodes_unclaimable_finished``
indexes only the tiny unclaimable slice, so both query shapes are
index-served.
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


def _explain(query: str, params: tuple = ()) -> str:
    with read_connection(TEST_DATABASE_URL) as conn:
        conn.execute("set enable_seqscan=off")
        rows = conn.execute("explain " + query, params).fetchall()
    return "\n".join(str(row["QUERY PLAN"]) for row in rows)


def test_unclaimable_finished_index_exists() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    assert "idx_job_nodes_unclaimable_finished" in _index_names()


def test_fleet_unclaimable_count_plans_on_partial_index() -> None:
    """The fleet-wide count must be served by the partial index, not a seq scan."""
    plan = _explain(
        "select count(*) as c from job_nodes"
        " where failure_detail='unclaimable_model' and finished_at >= %s",
        ("2020-01-01 00:00:00+00",),
    )
    assert "idx_job_nodes_unclaimable_finished" in plan
    assert "Seq Scan on job_nodes" not in plan


def test_workspace_unclaimable_count_never_seq_scans_job_nodes() -> None:
    """The workspace-scoped variant (exists probe into jobs) must stay index-served."""
    plan = _explain(
        "select count(*) as c from job_nodes"
        " where failure_detail='unclaimable_model' and finished_at >= %s"
        " and exists (select 1 from jobs j where j.id = job_nodes.job_id"
        " and j.workspace_id = %s)",
        ("2020-01-01 00:00:00+00", "ws-x"),
    )
    assert "Seq Scan on job_nodes" not in plan
