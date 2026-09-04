"""Schema v77: statement-level job status count triggers (#437).

The v36/v73 row-level triggers serialised every job status transition of one
run (or workspace) onto a few hot counter rows and deadlocked the claim path
under high concurrency. The v77 replacement is statement-level with
transition tables: one trigger per statement per event, aggregating the net
delta per (key, status) and applying it in fixed sorted order.

These tests pin the counter correctness the old tests already covered
(insert/update/delete/rebind/no-op update — the existing
test_run_job_status_counts_migration.py and
test_job_status_counts_migration.py keep running against the new shape), the
NEW statement-level guarantees (single-statement multi-row net deltas,
in-statement status swaps that cancel out), and the concurrency property
the fix exists for: interleaved competing UPDATEs on one run's jobs from two
connections must complete without a single SQLSTATE 40P01 and leave the
counters equal to the group-by.
"""

from __future__ import annotations

import threading

import psycopg

from server.app.db.schema import SCHEMA_VERSION
from server.app.db.transaction import read_connection, write_transaction
from tests.postgres_support import TEST_DATABASE_URL

# Two rows of headroom for the one-statement swap pairs below.
_SEED_JOB_COUNT = 6


def _seed(conn, workspace_id: str, run_id: str, count: int = _SEED_JOB_COUNT) -> list[str]:
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key)"
        " values (%s, %s, 'question_comprehension_info') on conflict do nothing",
        (workspace_id, workspace_id),
    )
    conn.execute(
        "insert into runs(id, workspace_id, source_kind)"
        " values (%s, %s, 'items') on conflict do nothing",
        (run_id, workspace_id),
    )
    ids = [f"sc76-{run_id}-{i}" for i in range(count)]
    for job_id in ids:
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id, run_id, status)"
            " values (%s, %s, 'test', %s, %s, 'queued')",
            (job_id, workspace_id, job_id, run_id),
        )
    return ids


def _group_by(conn, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, count(*) as cnt from jobs where run_id=%s group by status",
        (run_id,),
    ).fetchall()
    return {row["status"]: int(row["cnt"]) for row in rows}


def _run_counts(conn, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, cnt from run_job_status_counts where run_id=%s and cnt<>0",
        (run_id,),
    ).fetchall()
    return {row["status"]: int(row["cnt"]) for row in rows}


def _workspace_counts(conn, workspace_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, cnt from workspace_job_status_counts where workspace_id=%s and cnt<>0",
        (workspace_id,),
    ).fetchall()
    return {row["status"]: int(row["cnt"]) for row in rows}


def _workspace_group_by(conn, workspace_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, count(*) as cnt from jobs where workspace_id=%s group by status",
        (workspace_id,),
    ).fetchall()
    return {row["status"]: int(row["cnt"]) for row in rows}


def test_statement_triggers_are_statement_level() -> None:
    # The v77 shape: three single-event STATEMENT triggers per family; the
    # legacy row-level trigger names must be gone.
    with read_connection(TEST_DATABASE_URL) as conn:
        rows = conn.execute(
            "select trigger_name, action_orientation from information_schema.triggers"
            " where trigger_schema=current_schema() and event_object_table='jobs'"
            " order by trigger_name"
        ).fetchall()
    by_name = {str(row["trigger_name"]): str(row["action_orientation"]) for row in rows}
    for name in (
        "jobs_run_status_counts_sync_insert",
        "jobs_run_status_counts_sync_update",
        "jobs_run_status_counts_sync_delete",
        "jobs_status_counts_sync_insert",
        "jobs_status_counts_sync_update",
        "jobs_status_counts_sync_delete",
    ):
        assert by_name.get(name) == "STATEMENT", (name, by_name)
    assert "jobs_run_status_counts_sync" not in by_name
    assert "jobs_status_counts_sync" not in by_name


def test_single_statement_multi_row_net_delta() -> None:
    # One UPDATE flipping many rows across statuses is ONE trigger fire with
    # one aggregated delta per (run_id, status) — the v77 batching property.
    with write_transaction(TEST_DATABASE_URL) as conn:
        ids = _seed(conn, "sc76-ws-net", "sc76-run-net")
        conn.execute(
            "update jobs set status='running' where run_id='sc76-run-net' and id <= %s",
            (ids[2],),
        )
        assert _run_counts(conn, "sc76-run-net") == {"queued": 3, "running": 3}
        # A mixed transition in one statement: some to completed, some to failed.
        conn.execute(
            "update jobs set status = case when id <= %s then 'completed' else 'failed' end"
            " where run_id='sc76-run-net' and status='running'",
            (ids[1],),
        )
        expected = _group_by(conn, "sc76-run-net")
        assert _run_counts(conn, "sc76-run-net") == expected
        assert _workspace_counts(conn, "sc76-ws-net") == _workspace_group_by(conn, "sc76-ws-net")


def test_status_swap_in_one_statement_nets_to_zero() -> None:
    # Two rows swapping statuses inside ONE statement: the old-side and
    # new-side aggregations cancel per status — net delta zero, counters
    # unchanged. The row-level trigger produced the same end state via two
    # decrements + two increments; the net-delta path must not drift.
    with write_transaction(TEST_DATABASE_URL) as conn:
        ids = _seed(conn, "sc76-ws-swap", "sc76-run-swap")
        conn.execute(
            "update jobs set status='running' where run_id='sc76-run-swap' and id in (%s, %s)",
            (ids[0], ids[1]),
        )
        before = _run_counts(conn, "sc76-run-swap")
        conn.execute(
            "update jobs set status = case id when %s then 'queued' else 'completed' end"
            " where run_id='sc76-run-swap' and id in (%s, %s)",
            (ids[0], ids[0], ids[1]),
        )
        after = _run_counts(conn, "sc76-run-swap")
    assert before == {"queued": 4, "running": 2}
    assert after == {"queued": 5, "completed": 1}


def test_noop_and_cross_dimension_updates_leave_counters_untouched() -> None:
    # UPDATE of an unrelated column fires the statement trigger (plain AFTER
    # UPDATE, no column list) but the net-delta aggregation must filter the
    # unchanged (key, status) pairs to zero.
    with write_transaction(TEST_DATABASE_URL) as conn:
        ids = _seed(conn, "sc76-ws-noop", "sc76-run-noop")
        conn.execute("update jobs set status='running' where id=%s", (ids[0],))
        before_run = _run_counts(conn, "sc76-run-noop")
        before_ws = _workspace_counts(conn, "sc76-ws-noop")
        # Unrelated column only.
        conn.execute("update jobs set title='x' where run_id='sc76-run-noop'")
        # Same status re-set.
        conn.execute("update jobs set status='running' where id=%s", (ids[0],))
    assert _run_counts_safe("sc76-run-noop") == before_run
    assert _workspace_counts_safe("sc76-ws-noop") == before_ws


def _run_counts_safe(run_id: str) -> dict[str, int]:
    with read_connection(TEST_DATABASE_URL) as conn:
        return _run_counts(conn, run_id)


def _workspace_counts_safe(workspace_id: str) -> dict[str, int]:
    with read_connection(TEST_DATABASE_URL) as conn:
        return _workspace_counts(conn, workspace_id)


def test_interleaved_updates_neither_deadlock_nor_drift() -> None:
    # The #437 reproduction: two connections flip the SAME run's jobs between
    # opposite status directions with opposite scan orders — the exact shape
    # that deadlocked the v73 row trigger's two-step (subtract old, add new)
    # lock order. The v77 fixed-order delta application must complete without
    # 40P01 and leave counters == group-by.
    run_id = "sc76-run-race"
    with write_transaction(TEST_DATABASE_URL) as conn:
        ids = _seed(conn, "sc76-ws-race", run_id, count=24)
    errors: list[str] = []

    def flip(status: str, job_ids: list[str]) -> None:
        try:
            with psycopg.connect(TEST_DATABASE_URL) as conn:
                for job_id in job_ids:
                    with conn.transaction():
                        conn.execute("update jobs set status=%s where id=%s", (status, job_id))
        except psycopg.errors.DeadlockDetected as exc:
            errors.append(str(exc))

    threads = [
        threading.Thread(target=flip, args=("running", ids)),
        threading.Thread(target=flip, args=("completed", list(reversed(ids)))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [], f"deadlock under the v77 statement triggers: {errors}"
    with read_connection(TEST_DATABASE_URL) as conn:
        assert _run_counts(conn, run_id) == _group_by(conn, run_id)
        assert _workspace_counts(conn, "sc76-ws-race") == _workspace_group_by(conn, "sc76-ws-race")


def test_migration_recorded_as_v77() -> None:
    # The chain tail pin lives with the newest migration's module (v77).
    assert SCHEMA_VERSION == 77
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select name from schema_migrations where version=%s", (SCHEMA_VERSION,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "job_status_counts_statement_triggers"
