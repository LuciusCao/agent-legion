"""Schema v72: run_job_status_counts trigger-maintained counter table.

Issue #358: count_jobs_by_status_in_run fed the run detail endpoint's
job_stats with a group-by over the run's whole jobs slice — O(run jobs) per
refresh, a million-row scan on 10^6-item runs. The counter table
(DB-RUN-JOB-STATUS-COUNTS-001) turns the read into a PK lookup and is the
data source for the #350 run progress view.
"""

from __future__ import annotations

from server.app.db.migrations.run_job_status_counts import (
    migrate_run_job_status_counts,
)
from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs import JobQueries
from tests.postgres_support import TEST_DATABASE_URL


def _seed_workspace(conn, workspace_id: str) -> None:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key)"
        " values (%s, %s, 'question_comprehension_info') on conflict do nothing",
        (workspace_id, workspace_id),
    )


def _seed_run(conn, run_id: str, workspace_id: str) -> None:
    conn.execute(
        "insert into runs(id, workspace_id, source_kind) values (%s, %s, 'items')"
        " on conflict do nothing",
        (run_id, workspace_id),
    )


def _insert_job(conn, job_id: str, workspace_id: str, run_id: str, status: str) -> None:
    conn.execute(
        "insert into jobs(id, workspace_id, source_type, source_id, run_id, status)"
        " values (%s, %s, 'test', %s, %s, %s)",
        (job_id, workspace_id, job_id, run_id, status),
    )


def _run_group_by_counts(conn, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, count(*) as cnt from jobs where run_id = %s group by status",
        (run_id,),
    ).fetchall()
    return {row["status"]: int(row["cnt"]) for row in rows}


def _table_counts(conn, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, cnt from run_job_status_counts where run_id = %s and cnt <> 0",
        (run_id,),
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
                " and table_name='run_job_status_counts'"
            ).fetchall()
        }
        trigger = conn.execute(
            "select 1 from information_schema.triggers"
            " where trigger_schema=current_schema()"
            " and trigger_name='jobs_run_status_counts_sync'"
        ).fetchone()
    assert columns == {"run_id", "status", "cnt"}
    assert trigger is not None


def test_trigger_tracks_insert_update_delete_and_rebind() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "rjsc-ws")
        _seed_run(conn, "rjsc-run-1", "rjsc-ws")
        _seed_run(conn, "rjsc-run-2", "rjsc-ws")
        _insert_job(conn, "rjsc-1", "rjsc-ws", "rjsc-run-1", "queued")
        _insert_job(conn, "rjsc-2", "rjsc-ws", "rjsc-run-1", "queued")
        _insert_job(conn, "rjsc-3", "rjsc-ws", "rjsc-run-1", "running")
        # A job with no run (run_id='') must never touch the counter table.
        _insert_job(conn, "rjsc-orphan", "rjsc-ws", "", "queued")
        # Status transition: queued -> completed.
        conn.execute("update jobs set status='completed' where id='rjsc-1'")
        # Run rebind: the count must leave the old run entirely.
        conn.execute("update jobs set run_id='rjsc-run-2' where id='rjsc-3'")
        # Deletion decrements.
        conn.execute("delete from jobs where id='rjsc-2'")
        assert _table_counts(conn, "rjsc-run-1") == _run_group_by_counts(conn, "rjsc-run-1")
        assert _table_counts(conn, "rjsc-run-2") == _run_group_by_counts(conn, "rjsc-run-2")
        # No-run jobs keep the counter table untouched.
        assert (
            conn.execute(
                "select count(*) as n from run_job_status_counts where run_id=''"
            ).fetchone()["n"]
            == 0
        )
        # Updates that leave status/run_id unchanged must not drift counts.
        conn.execute("update jobs set title='x' where id='rjsc-1'")
        assert _table_counts(conn, "rjsc-run-1") == _run_group_by_counts(conn, "rjsc-run-1")


def test_run_delete_without_fk_stays_consistent() -> None:
    # No FK to runs (jobs.run_id is unconstrained text): deleting a run
    # leaves its counter rows orphaned, and the subsequent job deletes
    # (workspace deletion order, workspace.py) decrement them to zero —
    # invisible to the cnt<>0 read, so the counter never disagrees with the
    # group-by it replaced.
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "rjsc-del-ws")
        _seed_run(conn, "rjsc-del-run", "rjsc-del-ws")
        _insert_job(conn, "rjsc-del-1", "rjsc-del-ws", "rjsc-del-run", "queued")
        _insert_job(conn, "rjsc-del-2", "rjsc-del-ws", "rjsc-del-run", "completed")
        conn.execute("delete from runs where id='rjsc-del-run'")
        conn.execute("delete from jobs where run_id='rjsc-del-run'")
        nonzero = conn.execute(
            "select status, cnt from run_job_status_counts where run_id='rjsc-del-run' and cnt <> 0"
        ).fetchall()
        assert nonzero == []


def test_orphan_run_id_jobs_are_counted() -> None:
    # jobs.run_id has no FK by design (legacy/test rows reference run ids
    # with no runs row); the counter must track them like the group-by did.
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "rjsc-orphan-ws")
        _insert_job(conn, "rjsc-orph-1", "rjsc-orphan-ws", "no-such-run", "queued")
        assert _table_counts(conn, "no-such-run") == _run_group_by_counts(conn, "no-such-run")


def test_backfill_rebuilds_and_is_idempotent() -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "rjsc-bf-ws")
        _seed_run(conn, "rjsc-bf-run", "rjsc-bf-ws")
        for i in range(3):
            _insert_job(conn, f"rjsc-bf-{i}", "rjsc-bf-ws", "rjsc-bf-run", "completed")
        _insert_job(conn, "rjsc-bf-3", "rjsc-bf-ws", "rjsc-bf-run", "failed")
        # A no-run job must stay excluded from the backfill.
        _insert_job(conn, "rjsc-bf-orphan", "rjsc-bf-ws", "", "queued")
        # Wipe the counter table so the backfill is exercised from scratch.
        conn.execute("delete from run_job_status_counts")
        migrate_run_job_status_counts(conn)
        first = _table_counts(conn, "rjsc-bf-run")
        migrate_run_job_status_counts(conn)
        second = _table_counts(conn, "rjsc-bf-run")
    assert first == {"completed": 3, "failed": 1}
    assert second == first
    # run_id='' never gets a counter row.
    with read_connection(TEST_DATABASE_URL) as conn:
        orphan_rows = conn.execute(
            "select count(*) as n from run_job_status_counts where run_id=''"
        ).fetchone()
    assert orphan_rows["n"] == 0


def test_count_jobs_by_status_in_run_matches_group_by(tmp_path) -> None:
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed_workspace(conn, "rjsc-read-ws")
        _seed_run(conn, "rjsc-read-run", "rjsc-read-ws")
        _insert_job(conn, "rjsc-read-1", "rjsc-read-ws", "rjsc-read-run", "queued")
        _insert_job(conn, "rjsc-read-2", "rjsc-read-ws", "rjsc-read-run", "running")
        _insert_job(conn, "rjsc-read-3", "rjsc-read-ws", "rjsc-read-run", "completed")
        expected = _run_group_by_counts(conn, "rjsc-read-run")
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    assert queries.count_jobs_by_status_in_run("rjsc-read-run") == expected
    # A run with no jobs yields an empty mapping, same as the group-by.
    assert queries.count_jobs_by_status_in_run("rjsc-missing-run") == {}
    # The read is a PK lookup, not a scan of the run's jobs slice: pin the
    # plan shape so a future rewrite cannot silently regress to a group-by.
    with queries._connect_read() as conn:
        plan = conn.execute(
            "explain (costs off) select status, cnt from run_job_status_counts"
            " where run_id = %s and cnt <> 0",
            ("rjsc-read-run",),
        ).fetchall()
    plan_text = "\n".join(str(row[0]) if not isinstance(row, dict) else str(row) for row in plan)
    assert "run_job_status_counts_pkey" in plan_text
    assert "jobs" not in plan_text
