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

The v82 fix (two-level advisory hierarchy at trigger entry: class-82
pg_advisory_xact_lock on every distinct ws:<workspace> FIRST, then the
dimension keys, both sorted) serialises all counter writers per
workspace: B's stmt1 blocks on A's ws-gate advisory lock BEFORE touching
any counter row or dimension lock; when A commits, B proceeds and both
transactions complete — no ring can close because no two transactions
ever hold the same workspace's counter rows concurrently.
The test drives exactly that ordering: B blocks on the advisory lock while
A's later statements run; A commits (releasing the lock); B finishes and
commits. ``lock_timeout`` bounds every wait so a regression in the fix
fails fast instead of hanging the suite, and ``deadlock_timeout`` is set
LOW so a reintroduced ring is detected in milliseconds, not the 1s
production default.

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
# after the 1s production default. lock_timeout bounds every wait: B waiting
# on A's advisory lock is the FIX working (A commits within the window), but
# a broken fix (B waiting forever) must fail the test, not hang the suite.
_TIMEOUTS = ("set deadlock_timeout='50ms'", "set lock_timeout='5s'")

# Handshake window: B's stmt1 must be blocked on A's counter-row/advisory
# lock before A's stmt2 fires. The blocking is server-side after ~1ms; the
# window only needs to cover thread scheduling jitter.
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
        "select status, cnt from workspace_job_status_counts where workspace_id=%s and cnt<>0",
        (workspace_id,),
    ).fetchall()
    return {str(row["status"]): int(row["cnt"]) for row in rows}


def _run_counts(conn, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        "select status, cnt from run_job_status_counts where run_id=%s and cnt<>0",
        (run_id,),
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

    A runs its statements in order, pauses after the first so B's first
    statement is blocked server-side, then runs its remaining statements
    and COMMITS (releasing whatever B waits on — the advisory lock under
    the fix, the counter-row locks under v77). B then runs its remaining
    statements and commits. The interleaving is the #659 production shape:
    both transactions hold counter-row locks for one key while wanting each
    other's.
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
def test_workspace_counter_ring_is_serialised_by_advisory_lock() -> None:
    # The claim-batch shape: one workspace, one run, four queued jobs. A
    # (claim) promotes j1 then flips j3 to completed; B (rerun-shaped) flips
    # j2 to completed then promotes j4. A's stmt1 holds (ws,queued) +
    # (ws,running); B's stmt1 takes (ws,completed) and blocks on (ws,queued);
    # A's stmt2 wants (ws,completed) — the AB-BA ring on the workspace
    # counter rows. Under v82, B blocks on A's advisory lock instead (before
    # ANY counter row), A commits, B proceeds: both commit, no 40P01.
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
def test_run_counter_ring_is_serialised_by_advisory_lock() -> None:
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
def test_trigger_function_carries_the_advisory_lock() -> None:
    # Shape pin: the deployed counter trigger functions must take the
    # per-key advisory lock BEFORE any branch (the fix's entry prologue),
    # with family-distinct key prefixes so the run and workspace twins never
    # cross-lock. Guards a silent regression to the v77 body (e.g. a future
    # edit of the v77 module that v82 reuses slipping through unreviewed).
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as conn:
        rows = conn.execute(
            """
            select proname, prosrc
            from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = current_schema()
              and proname in ('sync_run_job_status_counts', 'sync_workspace_job_status_counts')
            """
        ).fetchall()
    by_name = {str(row["proname"]): str(row["prosrc"]) for row in rows}
    for fn, prefix in (
        ("sync_run_job_status_counts", "run:"),
        ("sync_workspace_job_status_counts", "ws:"),
    ):
        src = by_name[fn]
        assert "pg_advisory_xact_lock" in src, f"{fn} lost the advisory lock"
        # Two-int class form: the dedicated lock class 82 structurally
        # separates this keyspace from every single-bigint advisory user.
        assert "pg_advisory_xact_lock(82," in src, f"{fn} lost its lock class id"
        assert f"hashtext('{prefix}'" in src, f"{fn} lost its '{prefix}' keyspace prefix"
        # The prologue precedes every branch: the lock loop sits before the
        # first counter write.
        assert src.index("pg_advisory_xact_lock") < src.index("insert into"), fn
        assert src.index("pg_advisory_xact_lock") < src.index("update ", src.index("declare")), fn
        # TWO-LEVEL hierarchy: the ws: prologue precedes the dimension loop
        # for the run twin (swapping the levels would reopen the codex
        # cross-family ring; the ws twin's dimension loop is the same ws:
        # keyspace so the order check is trivially satisfied there).
        if prefix == "run:":
            assert src.index("hashtext('ws:'") < src.index("hashtext('run:'"), (
                f"{fn}: dimension lock taken before the ws prologue"
            )


@pytest.mark.postgres
def test_cross_family_ring_is_broken_by_the_lock_hierarchy() -> None:
    """codex review P1 的回归钉子：跨触发器家族的 AB-BA 环。PG 按名序
    触发（jobs_run_* 先于 jobs_status_*），单层锁序（每家族只锁自己的
    维度键）挡不住这个形态——A 的 stmt1 持 run:a + ws:x；B 的 stmt1
    （run-b 行）先锁 run:b、再在 ws 触发器等 ws:x；A 的 stmt2 摸 run-b
    的行，其 run 触发器等 run:b——环（单层形态实测 A 侧 40P01）。
    双层锁序（两家族都先锁全部 ws: 再锁维度键）让 B 在 ws: 入口排队，
    A 提交后 B 完成：零 40P01、双方落库、计数与 group-by 全等。

    A 的 stmt2 必须摸 run-b 的行——这是环的闭合边（此前版本摸 run-a
    自身键，re-entrant 不构成等待，环不闭合，revert-check 不红）。
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
            # stmt1（run-b 行）：run 触发器锁 run:b，ws 触发器等 A 的 ws:x。
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
        # stmt1（run-a 行）：A 持 run:a + ws:x 直到事务结束。
        conn_a.execute("update jobs set status='running' where id=%s", ("sc82-xf-1",))
        thread_b = threading.Thread(target=_b)
        thread_b.start()
        time.sleep(_B_BLOCK_WINDOW)  # B 已锁 run:b、阻塞在 ws:x
        # stmt2（run-b 行）：run 触发器等 run:b——单层形态在此闭合环。
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
