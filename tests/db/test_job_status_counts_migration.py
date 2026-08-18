"""Schema v36: workspace_job_status_counts trigger-maintained counter table."""

from __future__ import annotations

from server.app.db.migrations.job_status_counts import (
    migrate_workspace_job_status_counts,
)
from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs import JobQueries
from tests.postgres_support import TEST_DATABASE_URL


def _seed_workspace(conn, workspace_id: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key) values (%s, %s, 'question_comprehension_info') on conflict do nothing",
        (workspace_id, workspace_id),
    )


def _insert_job(conn, job_id: str, workspace_id: str, status: str) -> None:
    conn.execute(
        "insert into jobs(id, workspace_id, workflow_key, source_type, source_id, status)"
        " values (%s, %s, 'video_knowledge', 'test', %s, %s)",
        (job_id, workspace_id, job_id, status),
    )


def _group_by_counts(conn, workspace_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, count(*) as cnt from jobs where workspace_id = %s group by status",
        (workspace_id,),
    ).fetchall()
    return {row["status"]: int(row["cnt"]) for row in rows}


def _table_counts(conn, workspace_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, cnt from workspace_job_status_counts where workspace_id = %s and cnt <> 0",
        (workspace_id,),
    ).fetchall()
    return {row["status"]: int(row["cnt"]) for row in rows}


def test_counts_table_and_trigger_exist() -> None:
    # The autouse fixture already ran init_db at the current SCHEMA_VERSION.
    with read_connection(TEST_DATABASE_URL) as conn:
        columns = {
            row["column_name"]
            for row in conn.execute(
                "select column_name from information_schema.columns"
                " where table_schema=current_schema()"
                " and table_name='workspace_job_status_counts'"
            ).fetchall()
        }
        trigger = conn.execute(
            "select 1 from information_schema.triggers"
            " where trigger_schema=current_schema()"
            " and trigger_name='jobs_status_counts_sync'"
        ).fetchone()
    assert columns == {"workspace_id", "status", "cnt"}
    assert trigger is not None


def test_trigger_tracks_insert_update_delete() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "jsc-trig-ws")
        _seed_workspace(conn, "jsc-trig-ws2")
        _insert_job(conn, "jsc-trig-1", "jsc-trig-ws", "queued")
        _insert_job(conn, "jsc-trig-2", "jsc-trig-ws", "queued")
        _insert_job(conn, "jsc-trig-3", "jsc-trig-ws", "running")
        # Status transition: queued -> running.
        conn.execute("update jobs set status='running' where id='jsc-trig-1'")
        # Workspace move: counts must leave the old workspace entirely.
        conn.execute("update jobs set workspace_id='jsc-trig-ws2' where id='jsc-trig-3'")
        # Deletion decrements.
        conn.execute("delete from jobs where id='jsc-trig-2'")
        assert _table_counts(conn, "jsc-trig-ws") == _group_by_counts(conn, "jsc-trig-ws")
        assert _table_counts(conn, "jsc-trig-ws2") == _group_by_counts(conn, "jsc-trig-ws2")
        # Updates that leave status/workspace_id unchanged must not drift counts.
        conn.execute("update jobs set title='x' where id='jsc-trig-1'")
        assert _table_counts(conn, "jsc-trig-ws") == _group_by_counts(conn, "jsc-trig-ws")


def test_backfill_rebuilds_and_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "jsc-backfill-ws")
        for i in range(3):
            _insert_job(conn, f"jsc-bf-{i}", "jsc-backfill-ws", "completed")
        _insert_job(conn, "jsc-bf-3", "jsc-backfill-ws", "failed")
        # Wipe the counter table so the backfill is exercised from scratch.
        conn.execute("delete from workspace_job_status_counts")
        migrate_workspace_job_status_counts(conn)
        first = _table_counts(conn, "jsc-backfill-ws")
        migrate_workspace_job_status_counts(conn)
        second = _table_counts(conn, "jsc-backfill-ws")
    assert first == {"completed": 3, "failed": 1}
    assert second == first


def test_count_jobs_by_status_matches_group_by(tmp_path) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "jsc-read-ws")
        _insert_job(conn, "jsc-read-1", "jsc-read-ws", "queued")
        _insert_job(conn, "jsc-read-2", "jsc-read-ws", "pending")
        _insert_job(conn, "jsc-read-3", "jsc-read-ws", "running")
        _insert_job(conn, "jsc-read-4", "jsc-read-ws", "completed")
    with read_connection(TEST_DATABASE_URL) as conn:
        expected_raw = _group_by_counts(conn, "jsc-read-ws")
    # Read path merges queued into pending, mirroring the legacy group-by read.
    expected: dict[str, int] = {}
    for status, cnt in expected_raw.items():
        key = "pending" if status == "queued" else status
        expected[key] = expected.get(key, 0) + cnt
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    assert queries.count_jobs_by_status("jsc-read-ws") == expected
    # A workspace with no jobs yields an empty mapping, same as the group-by.
    assert queries.count_jobs_by_status("jsc-missing-ws") == {}
