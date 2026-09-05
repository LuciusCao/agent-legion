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
    # UPDATE, no column lists — transition tables forbid them) but the net-
    # delta aggregation must filter the unchanged (key, status) pairs:
    # ``having sum(cnt) <> 0`` drops zero-net pairs BEFORE the loop. The
    # old ``update of status, run_id`` shape skipped the trigger body
    # outright for these statements; the naive statement shape without the
    # HAVING still walked the loop with delta=0 — the positive-delta upsert
    # arm created phantom cnt=0 rows when a counter row was missing and
    # re-locked existing rows for a cnt+0 write (the completion flow's
    # outcome/packed updates, job_nodes.py:151 / workspace_packages.py:106,
    # fire this shape on every finished stream).
    with write_transaction(TEST_DATABASE_URL) as conn:
        ids = _seed(conn, "sc76-ws-noop", "sc76-run-noop")
        conn.execute("update jobs set status='running' where id=%s", (ids[0],))
        before_run = _run_counts(conn, "sc76-run-noop")
        before_ws = _workspace_counts(conn, "sc76-ws-noop")
        # Unrelated column only.
        conn.execute("update jobs set title='x' where run_id='sc76-run-noop'")
        # Same status re-set (statement carries the status column but the
        # value is unchanged — same net-zero pair, must be filtered too).
        conn.execute("update jobs set status='running' where id=%s", (ids[0],))
        # And the raw row content: no phantom rows, no counter churn.
        before_run_rows = conn.execute(
            "select status, cnt from run_job_status_counts"
            " where run_id='sc76-run-noop' order by status"
        ).fetchall()
        before_ws_rows = conn.execute(
            "select status, cnt from workspace_job_status_counts"
            " where workspace_id='sc76-ws-noop' order by status"
        ).fetchall()
    assert _run_counts_safe("sc76-run-noop") == before_run
    assert _workspace_counts_safe("sc76-ws-noop") == before_ws
    assert [(str(r["status"]), int(r["cnt"])) for r in before_run_rows] == [
        ("queued", 5),
        ("running", 1),
    ]
    assert [(str(r["status"]), int(r["cnt"])) for r in before_ws_rows] == [
        ("queued", 5),
        ("running", 1),
    ]


def test_noop_update_creates_no_phantom_row_when_counter_missing() -> None:
    # The zero-net filter's missing-row half: a title/updated_at-only UPDATE
    # against a (run_id, status) whose counter row does NOT exist must not
    # create one. Without ``having sum(cnt) <> 0`` the delta=0 pair fell
    # into the positive-delta upsert arm (delta<0 is false) and INSERTed a
    # phantom cnt=0 row — the old column-filter shape never ran the body at
    # all, so no row may appear. Checked via raw rows, not the cnt<>0 read
    # helpers, because a phantom cnt=0 row hides behind exactly that filter.
    with write_transaction(TEST_DATABASE_URL) as conn:
        _seed(conn, "sc76-ws-phantom", "sc76-run-phantom")
        # Drop the run counter rows entirely, then run a completion-flow
        # shaped UPDATE (outcome + updated_at, no status/run_id column).
        conn.execute("delete from run_job_status_counts where run_id='sc76-run-phantom'")
        conn.execute(
            "update jobs set outcome='done', updated_at=current_timestamp"
            " where run_id='sc76-run-phantom'"
        )
        run_rows = conn.execute(
            "select status, cnt from run_job_status_counts where run_id='sc76-run-phantom'"
        ).fetchall()
    assert run_rows == []


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


def test_net_negative_delta_on_missing_row_creates_no_row() -> None:
    # Old-shape parity (#441 review P2): a negative net delta on a (key,
    # status) pair whose counter row does not exist must be a NO-OP — the
    # v36/v73 row triggers applied negative deltas as bare UPDATEs, which
    # match nothing on a missing row. The naive single-upsert shape (INSERT
    # ... ON CONFLICT DO UPDATE) would take the insert branch and CREATE a
    # cnt=-1 row (verified on PG 17.11); the sign-split apply keeps the
    # old semantics. Unreachable through normal writes (the INSERT arm
    # always creates the row when the job is inserted) but pinned anyway —
    # the trigger must not invent counter rows.
    with write_transaction(TEST_DATABASE_URL) as conn:
        ids = _seed(conn, "sc76-ws-neg", "sc76-run-neg")
        # Remove the counter rows so the status flip below produces a net
        # -1 on ('queued', missing) and a net +1 on ('running', missing).
        # Only the run rows are deleted — the workspace rows (5 remaining
        # queued + 1 running) stay, so the workspace arm is a mixed
        # net-negative-on-existing check.
        conn.execute("delete from run_job_status_counts where run_id='sc76-run-neg'")
        conn.execute("update jobs set status='running' where id=%s", (ids[0],))
        run_rows = conn.execute(
            "select status, cnt from run_job_status_counts where run_id='sc76-run-neg'"
        ).fetchall()
        ws_rows = conn.execute(
            "select status, cnt from workspace_job_status_counts where workspace_id='sc76-ws-neg'"
        ).fetchall()
    # The missing negative side created no row: exactly one run counter row
    # exists (the positive side), no phantom queued=-1.
    assert [(str(r["status"]), int(r["cnt"])) for r in run_rows] == [("running", 1)]
    # Workspace rows were never deleted: queued went 6 -> 5 (existing-row
    # decrement), running 0 -> 1 (positive upsert on existing zero row).
    assert [(str(r["status"]), int(r["cnt"])) for r in ws_rows] == [
        ("queued", 5),
        ("running", 1),
    ]
    # The deleted run rows are NOT rebuilt by later writes (no INSERT
    # happened for the still-queued jobs) — the run counts read shows only
    # the statuses whose rows exist with cnt<>0. Parity holds for the
    # statuses that were re-created; the missing queued row is the exact
    # old-shape behavior being pinned (bare UPDATE on missing = no-op).
    with read_connection(TEST_DATABASE_URL) as conn:
        assert _run_counts(conn, "sc76-run-neg") == {"running": 1}
        assert _workspace_counts(conn, "sc76-ws-neg") == _workspace_group_by(conn, "sc76-ws-neg")


def test_net_negative_delta_decrements_existing_row() -> None:
    # The sign-split's other half: a negative net delta on an EXISTING
    # counter row must still decrement it (a WHERE-guarded DO UPDATE that
    # skips negative deltas would silently drop the decrement — verified
    # wrong on PG 17.11 — so the guard is a bare UPDATE, not an upsert
    # condition).
    with write_transaction(TEST_DATABASE_URL) as conn:
        ids = _seed(conn, "sc76-ws-neg2", "sc76-run-neg2")
        conn.execute(
            "update jobs set status='running' where run_id='sc76-run-neg2' and id in (%s, %s)",
            (ids[0], ids[1]),
        )
        # 2 running; now flip both back: net -2 on running, +2 on queued.
        conn.execute(
            "update jobs set status='queued' where run_id='sc76-run-neg2' and status='running'"
        )
    with read_connection(TEST_DATABASE_URL) as conn:
        # cnt<>0-inclusive check: the running row legitimately sits at 0
        # and must not go negative.
        rows = conn.execute(
            "select status, cnt from run_job_status_counts where run_id='sc76-run-neg2'"
        ).fetchall()
        assert {(str(r["status"]), int(r["cnt"])) for r in rows} == {
            ("queued", 6),
            ("running", 0),
        }
        assert _run_counts(conn, "sc76-run-neg2") == _group_by(conn, "sc76-run-neg2")
        assert _workspace_counts(conn, "sc76-ws-neg2") == _workspace_group_by(conn, "sc76-ws-neg2")


def test_migration_recorded_as_v77() -> None:
    # The chain-tail pin moved to the v78 module
    # (tests/db/test_claim_stage_profile_migration.py); this file keeps its
    # own v77 row pin.
    assert SCHEMA_VERSION >= 77
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select name from schema_migrations where version=77").fetchone()
    assert row is not None
    assert row["name"] == "job_status_counts_statement_triggers"
