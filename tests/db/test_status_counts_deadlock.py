"""Schema v82 regression: the multi-statement counter deadlock ring (#659).

The v77 statement-level rebuild (#437) fixed the ring WITHIN one statement —
every firing applies its net deltas in a fixed (key, status) sorted order.
#659's production evidence showed the ring survives ACROSS statements inside
one transaction: a claim batch promotes several executions, psycopg
executemany issues each promote as its own UPDATE, and the transaction fires
the counter trigger once per statement, each firing locking its (key, status)
rows in sorted order but the SEQUENCE of lock sets varying with the business
order. Two such transactions interleaving close an AB-BA ring on the counter
rows (claim vs. rerun, one workspace's hot rows): PG's detector breaks it
after 1s, the client retries — the claim/heartbeat/rerun 500 waves and the
result-commit 409 waves of #659 (first POST commits, response lost in the
lock queue; retried POST hits the terminal state) are this ring plus its
downstream symptom.

The ring shape pinned here (both arms reproducible on demand, 100% of rounds
on the v77 shape — verified while writing this file):

- The deadlock needs DIFFERENT transition mixes per statement, so the two
  transactions' sorted lock orders START on different rows: A's stmt1
  promotes j1 queued->running (takes (k,queued) then (k,running)); B's stmt1
  flips j2 queued->completed (takes (k,completed) then blocks on (k,queued)
  held by A); A's stmt2 flips j3 queued->completed (wants (k,completed) held
  by B) — ring closed. Identical per-statement mixes (both promote) cannot
  ring: the sorted order then agrees on the first row, so one side blocks
  outright — this is why #437's own interleaved-promote test never caught it.
- Workspace arm: one workspace, all four jobs on one run (the claim-batch
  shape — several promotes of one run inside one claim transaction).
- Run arm: two runs in one workspace; A's statements touch run-a, B's
  run-b. The run-level counter rows are disjoint, but every jobs UPDATE
  fires BOTH twins, so the mixed per-statement lock orders race through the
  workspace twin's shared (workspace, status) rows and the run twin's own
  rows in the same interleave — the rerun-vs-result shape of #659.

The v82 fix appends each statement's net changes to delta tables and uses
``pg_try_advisory_xact_lock`` only to elect an opportunistic folder. A loser
never waits and never touches the shared base counter rows; reads sum base +
pending deltas in one snapshot. This removes both the original cross-statement
counter-row ring and the row-lock × blocking-advisory ring an AFTER-trigger
gate would introduce. ``lock_timeout`` bounds any unexpected wait and
``deadlock_timeout`` makes a reintroduced ring fail in milliseconds.

The trigger shape under test is whatever init_db deployed (the autouse
postgres fixture builds the schema at SCHEMA_VERSION): the assertions are
the #659 contract — no SQLSTATE 40P01, both transactions commit, and the
counters still equal the group-by. The revert-check (temporarily re-creating
the v77 functions) makes the same assertions fail; that check was run while
writing this file and is the reason the mix statements below are exactly
these statuses.
"""

from __future__ import annotations

import contextlib
import threading
import time

import psycopg
import pytest

from server.app.db.rows import string_dict_row
from tests.postgres_support import TEST_DATABASE_URL

# Low deadlock_timeout: a reintroduced ring must be DETECTED fast (ms), not
# after the 1s production default. lock_timeout bounds every unexpected wait.
_TIMEOUTS = ("set deadlock_timeout='50ms'", "set lock_timeout='5s'")

# Handshake window: keep A's transaction open long enough for B to exercise
# the same overlap that used to block on A's counter-row/advisory lock.
_B_BLOCK_WINDOW = 0.3


def _seed(conn, workspace_id: str, runs: dict[str, tuple[str, ...]]) -> None:
    """Seed one workspace with the given runs (id -> job ids), all queued."""
    conn.execute(
        "insert into workspaces(id, name, default_workflow_key)"
        " values (%s, %s, 'question_comprehension_info') on conflict do nothing",
        (workspace_id, workspace_id),
    )
    for run_id, job_ids in runs.items():
        conn.execute(
            "insert into runs(id, workspace_id, source_kind)"
            " values (%s, %s, 'items') on conflict do nothing",
            (run_id, workspace_id),
        )
        for job_id in job_ids:
            conn.execute(
                "insert into jobs(id, workspace_id, source_type, source_id, run_id, status)"
                " values (%s, %s, 'test', %s, %s, 'queued')",
                (job_id, workspace_id, job_id, run_id),
            )


def _group_by(conn, workspace_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, count(*) as cnt from jobs where workspace_id=%s group by status",
        (workspace_id,),
    ).fetchall()
    return {str(row["status"]): int(row["cnt"]) for row in rows}


def _workspace_counts(conn, workspace_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, sum(cnt) as cnt from ("
        " select status, cnt from workspace_job_status_counts where workspace_id=%s"
        " union all"
        " select status, delta as cnt from workspace_job_status_count_deltas"
        " where workspace_id=%s"
        ") counts group by status having sum(cnt)<>0",
        (workspace_id, workspace_id),
    ).fetchall()
    return {str(row["status"]): int(row["cnt"]) for row in rows}


def _run_counts(conn, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, sum(cnt) as cnt from ("
        " select status, cnt from run_job_status_counts where run_id=%s"
        " union all"
        " select status, delta as cnt from run_job_status_count_deltas where run_id=%s"
        ") counts group by status having sum(cnt)<>0",
        (run_id, run_id),
    ).fetchall()
    return {str(row["status"]): int(row["cnt"]) for row in rows}


def _run_group_by(conn, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, count(*) as cnt from jobs where run_id=%s group by status",
        (run_id,),
    ).fetchall()
    return {str(row["status"]): int(row["cnt"]) for row in rows}


def _deadlock_or_timeout(exc: psycopg.Error) -> str:
    """Classify a racing transaction's failure: the only two outcomes a
    correct fix forbids (40P01 ring; lock_timeout = a wait that never
    resolved within the bound)."""
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate == "40P01":
        return "deadlock"
    if sqlstate == "55P03":
        return "lock_timeout"
    return f"{type(exc).__name__}:{sqlstate}"


def _race(
    a_statements: tuple[tuple[str, tuple[str, ...]], ...],
    b_statements: tuple[tuple[str, tuple[str, ...]], ...],
) -> tuple[list[str], list[str]]:
    """Run two interleaved multi-statement transactions and return their
    failure classifications ([] == committed clean).

    A runs its statements in order and pauses after the first while B runs.
    On v77 this interleave closes the #659 counter-row ring; on the rejected
    blocking-gate v82 shape B waits on A's advisory lock and can close a
    jobs-row/gate ring. The non-blocking delta shape lets B proceed without
    touching A's base-counter locks.
    """
    failures: dict[str, list[str]] = {"a": [], "b": []}

    def run_tx(side: str, statements: tuple[tuple[str, tuple[str, ...]], ...]) -> None:
        conn = psycopg.connect(TEST_DATABASE_URL, autocommit=False, row_factory=string_dict_row)
        try:
            for timeout in _TIMEOUTS:
                conn.execute(timeout)
            for sql, params in statements:
                conn.execute(sql, params)
            conn.commit()
        except psycopg.Error as exc:  # pragma: no cover - classification path
            failures[side].append(_deadlock_or_timeout(exc))
            with contextlib.suppress(psycopg.Error):
                conn.rollback()
        finally:
            conn.close()

    thread_b = threading.Thread(target=run_tx, args=("b", b_statements))
    # A's first statement lands first (holds the key's counter rows /
    # advisory lock); B starts and blocks; A finishes and commits.
    run_tx_partial = a_statements
    conn_a = psycopg.connect(TEST_DATABASE_URL, autocommit=False, row_factory=string_dict_row)
    try:
        for timeout in _TIMEOUTS:
            conn_a.execute(timeout)
        sql, params = run_tx_partial[0]
        conn_a.execute(sql, params)
        thread_b.start()
        time.sleep(_B_BLOCK_WINDOW)
        for sql, params in run_tx_partial[1:]:
            conn_a.execute(sql, params)
        conn_a.commit()
    except psycopg.Error as exc:
        failures["a"].append(_deadlock_or_timeout(exc))
        with contextlib.suppress(psycopg.Error):
            conn_a.rollback()
    finally:
        conn_a.close()
    thread_b.join(timeout=30)
    # B hanging past the join bound must fail loudly (its failures list
    # would read as empty otherwise — a false pass).
    assert not thread_b.is_alive(), "B-side transaction never resolved"
    return failures["a"], failures["b"]


@pytest.mark.postgres
def test_workspace_counter_ring_has_no_waiting_edge() -> None:
    # The claim-batch shape: one workspace, one run, four queued jobs. A
    # (claim) promotes j1 then flips j3 to completed; B (rerun-shaped) flips
    # j2 to completed then promotes j4. A's stmt1 holds (ws,queued) +
    # (ws,running); B's stmt1 takes (ws,completed) and blocks on (ws,queued);
    # A's stmt2 wants (ws,completed) — the AB-BA ring on the workspace
    # counter rows. Under v82, B appends deltas and skips folding while A owns
    # the try-lock: both commit without a waiting edge or 40P01.
    workspace = "sc82-ws-ring"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as seed:
        seed.execute("delete from jobs where id like 'sc82-ws-%'")
        seed.execute("delete from run_job_status_counts where run_id='sc82-ws-run'")
        seed.execute("delete from workspace_job_status_counts where workspace_id=%s", (workspace,))
        seed.execute("delete from runs where id='sc82-ws-run'")
        seed.execute("delete from workspaces where id=%s", (workspace,))
        _seed(
            seed, workspace, {"sc82-ws-run": ("sc82-ws-1", "sc82-ws-2", "sc82-ws-3", "sc82-ws-4")}
        )

    a_failures, b_failures = _race(
        a_statements=(
            ("update jobs set status='running' where id=%s", ("sc82-ws-1",)),
            ("update jobs set status='completed' where id=%s", ("sc82-ws-3",)),
        ),
        b_statements=(
            ("update jobs set status='completed' where id=%s", ("sc82-ws-2",)),
            ("update jobs set status='running' where id=%s", ("sc82-ws-4",)),
        ),
    )
    assert a_failures == [], f"claim-side transaction failed: {a_failures}"
    assert b_failures == [], f"rerun-side transaction failed: {b_failures}"

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as check:
        assert _workspace_counts(check, workspace) == _group_by(check, workspace)
        assert _run_counts(check, "sc82-ws-run") == _run_group_by(check, "sc82-ws-run")


@pytest.mark.postgres
def test_run_counter_ring_has_no_waiting_edge() -> None:
    # The run-twin's own ring: two runs in one workspace. A's statements
    # touch run-a (promote j1, then flip j3 to completed); B's touch run-b
    # (flip j2 to completed, then promote j4). The run counter rows are
    # disjoint, but each statement also fires the workspace twin on the SAME
    # (workspace, status) rows — the mixed per-statement orders close the
    # ring through whichever twin locks first (verified: the v77 shape
    # deadlocks 6/6 on this exact interleave; v82 0/6).
    workspace = "sc82-run-ring"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as seed:
        seed.execute("delete from jobs where id like 'sc82-run-%'")
        seed.execute("delete from run_job_status_counts where run_id like 'sc82-run-%'")
        seed.execute("delete from workspace_job_status_counts where workspace_id=%s", (workspace,))
        seed.execute("delete from runs where id like 'sc82-run-%'")
        seed.execute("delete from workspaces where id=%s", (workspace,))
        _seed(
            seed,
            workspace,
            {
                "sc82-run-a": ("sc82-run-1", "sc82-run-3"),
                "sc82-run-b": ("sc82-run-2", "sc82-run-4"),
            },
        )

    a_failures, b_failures = _race(
        a_statements=(
            ("update jobs set status='running' where id=%s", ("sc82-run-1",)),
            ("update jobs set status='completed' where id=%s", ("sc82-run-3",)),
        ),
        b_statements=(
            ("update jobs set status='completed' where id=%s", ("sc82-run-2",)),
            ("update jobs set status='running' where id=%s", ("sc82-run-4",)),
        ),
    )
    assert a_failures == [], f"run-a transaction failed: {a_failures}"
    assert b_failures == [], f"run-b transaction failed: {b_failures}"

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as check:
        assert _workspace_counts(check, workspace) == _group_by(check, workspace)
        for run_id in ("sc82-run-a", "sc82-run-b"):
            assert _run_counts(check, run_id) == _run_group_by(check, run_id)


@pytest.mark.postgres
def test_trigger_function_uses_non_blocking_delta_folds() -> None:
    """Shape pin: losers append exact deltas and never wait on a gate."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as conn:
        rows = conn.execute(
            """
            select proname, prosrc
            from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = current_schema()
              and proname in (
                'sync_run_job_status_counts', 'sync_workspace_job_status_counts',
                'try_fold_run_job_status_counts', 'try_fold_workspace_job_status_counts'
              )
            """
        ).fetchall()
    by_name = {str(row["proname"]): str(row["prosrc"]) for row in rows}
    run_sync = by_name["sync_run_job_status_counts"]
    ws_sync = by_name["sync_workspace_job_status_counts"]
    run_fold = by_name["try_fold_run_job_status_counts"]
    ws_fold = by_name["try_fold_workspace_job_status_counts"]
    assert "insert into run_job_status_count_deltas" in run_sync
    assert "insert into workspace_job_status_count_deltas" in ws_sync
    assert "pg_try_advisory_xact_lock(82" in run_fold
    assert "pg_try_advisory_xact_lock(83" in run_fold
    assert "pg_try_advisory_xact_lock(82" in ws_fold
    assert "delete from run_job_status_count_deltas" in run_fold
    assert "delete from workspace_job_status_count_deltas" in ws_fold
    assert "returning status, delta" in run_fold
    assert "returning status, delta" in ws_fold
    assert "pg_advisory_xact_lock" not in "".join(by_name.values())


@pytest.mark.postgres
def test_cross_family_ring_has_no_waiting_edge() -> None:
    """Run/workspace trigger overlap must never wait on another folder.

    The old per-family lock design let A hold run:a + ws:x while B held
    run:b and waited for ws:x; A's next run-b statement closed the ring.
    Try-lock losers now leave their deltas pending, so B never owns one side
    of a cross-family wait cycle.
    """
    workspace = "sc82-xfam"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as seed:
        seed.execute("delete from jobs where id like 'sc82-xf-%'")
        seed.execute("delete from run_job_status_counts where run_id like 'sc82-xf-%'")
        seed.execute("delete from workspace_job_status_counts where workspace_id=%s", (workspace,))
        seed.execute("delete from runs where id like 'sc82-xf-%'")
        seed.execute("delete from workspaces where id=%s", (workspace,))
        _seed(
            seed,
            workspace,
            {
                "sc82-xf-a": ("sc82-xf-1", "sc82-xf-3"),
                "sc82-xf-b": ("sc82-xf-2", "sc82-xf-4"),
            },
        )

    results: dict[str, BaseException | None] = {}

    def _b() -> None:
        conn = psycopg.connect(TEST_DATABASE_URL, autocommit=False)
        try:
            for timeout in _TIMEOUTS:
                conn.execute(timeout)
            # The run-b statements overlap A's open run-a transaction.
            conn.execute("update jobs set status='completed' where id=%s", ("sc82-xf-2",))
            conn.execute("update jobs set status='running' where id=%s", ("sc82-xf-4",))
            conn.commit()
            results["b"] = None
        except psycopg.Error as exc:
            results["b"] = exc
            with contextlib.suppress(psycopg.Error):
                conn.rollback()
        finally:
            conn.close()

    conn_a = psycopg.connect(TEST_DATABASE_URL, autocommit=False)
    try:
        for timeout in _TIMEOUTS:
            conn_a.execute(timeout)
        # A keeps both folder gates until its transaction ends.
        conn_a.execute("update jobs set status='running' where id=%s", ("sc82-xf-1",))
        thread_b = threading.Thread(target=_b)
        thread_b.start()
        time.sleep(_B_BLOCK_WINDOW)
        # Touch run-b while B is active: this used to close the family ring.
        conn_a.execute("update jobs set status='completed' where id=%s", ("sc82-xf-4",))
        conn_a.commit()
        results["a"] = None
    except psycopg.Error as exc:
        results["a"] = exc
        with contextlib.suppress(psycopg.Error):
            conn_a.rollback()
    finally:
        conn_a.close()
        thread_b.join(timeout=30)
        assert not thread_b.is_alive(), "B-side transaction never resolved"

    outcomes = list(results.values())
    deadlocked = [e for e in outcomes if isinstance(e, psycopg.errors.DeadlockDetected)]
    assert deadlocked == [], [getattr(e, "sqlstate", e) for e in outcomes]
    assert outcomes == [None, None], outcomes

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as check:
        assert _workspace_counts(check, workspace) == _group_by(check, workspace)
        for run_id in ("sc82-xf-a", "sc82-xf-b"):
            assert _run_counts(check, run_id) == _run_group_by(check, run_id)


@pytest.mark.postgres
def test_try_lock_loser_commits_exact_pending_deltas() -> None:
    """A try-lock loser commits promptly and remains visible to readers."""
    workspace = "sc82-pending"
    run_id = "sc82-pending-run"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as seed:
        _seed(seed, workspace, {run_id: ("sc82-pending-a", "sc82-pending-b")})

    b_done = threading.Event()
    b_failures: list[str] = []

    def _b() -> None:
        conn = psycopg.connect(TEST_DATABASE_URL, autocommit=False)
        try:
            for timeout in _TIMEOUTS:
                conn.execute(timeout)
            conn.execute("update jobs set status='completed' where id='sc82-pending-b'")
            conn.commit()
        except psycopg.Error as exc:  # pragma: no cover - failure detail
            b_failures.append(_deadlock_or_timeout(exc))
            with contextlib.suppress(psycopg.Error):
                conn.rollback()
        finally:
            conn.close()
            b_done.set()

    conn_a = psycopg.connect(TEST_DATABASE_URL, autocommit=False)
    try:
        for timeout in _TIMEOUTS:
            conn_a.execute(timeout)
        conn_a.execute("update jobs set status='running' where id='sc82-pending-a'")
        thread_b = threading.Thread(target=_b)
        thread_b.start()
        assert b_done.wait(timeout=5), "try-lock loser waited instead of committing its delta"
        assert b_failures == []

        # A is still uncommitted. One snapshot must see B's committed jobs row
        # and matching pending delta, but none of A's private changes.
        with psycopg.connect(
            TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row
        ) as check:
            assert _workspace_counts(check, workspace) == _group_by(check, workspace)
            assert _run_counts(check, run_id) == _run_group_by(check, run_id)

        conn_a.commit()
        thread_b.join(timeout=5)
    finally:
        conn_a.close()

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as check:
        assert _workspace_counts(check, workspace) == _group_by(check, workspace)
        assert _run_counts(check, run_id) == _run_group_by(check, run_id)
        # A no-op status transition still invokes the statement triggers and
        # lets the next winner fold the committed tail left by B.
        check.execute("update jobs set title='fold-tail' where id='sc82-pending-a'")
        ws_pending = check.execute(
            "select count(*) as n from workspace_job_status_count_deltas where workspace_id=%s",
            (workspace,),
        ).fetchone()
        run_pending = check.execute(
            "select count(*) as n from run_job_status_count_deltas where run_id=%s",
            (run_id,),
        ).fetchone()
        assert int(ws_pending["n"]) == 0
        assert int(run_pending["n"]) == 0
