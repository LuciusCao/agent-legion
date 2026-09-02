"""Concurrent shard-finish serialization (EXEC-SHARD-001, lost-update regression).

``on_shard_finished`` computes the node aggregate from the shard rows inside
the caller's READ COMMITTED write transaction. Two finish transactions for
the last two shards used to race: each saw the peer's update as in flight,
both computed a non-terminal aggregate, and the node wedged in ``running``
with every shard committed terminal — deterministically reproducible and
unrecoverable (no reconciliation path reads the aggregate outside the finish
path; ``claim_shard_node`` only claims ``pending`` shards).

The finish path now takes ordered row locks on the node's shards before
updating, so concurrent finishers serialize: the second one's aggregate read
observes the first one's committed status. The tests below force the losing
interleaving deterministically — the first finisher holds its transaction
open (locks held, own update visible to itself only) while the second
finisher enters, and the harness only lets the first one commit once the
second one has either finished its aggregate read or verifiably parked on
the first one's row locks (see ``_run_first_then_second``).

Detection split, verified by removing the locks: the ``completed``-pair
tests are the lost-update detectors (they fail without the row locks); the
mixed ``completed``/``failed`` tests are aggregate-semantics invariants only
— a failed finisher's own UPDATE puts ``failed`` into the aggregate under
any interleaving, so they pass either way and pin the precedence order
(``failed`` wins over ``running``), they do not detect the race.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from server.app.db.schema import init_db
from server.app.db.transaction import read_connection, write_transaction
from server.app.executors._lease_shards import finish_shard_execution
from server.app.executors.models import ExecutionResult
from server.app.workflows.sharding import materialize_shards, on_shard_finished
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres

# Postgres lock waits are unbounded by default; the joins below only need to
# catch a hang, so they get generous slack over the event timeouts.
_JOIN_TIMEOUT = 30.0
# Fallback for the handshake poll: with the fix the second finisher parks on
# the first one's row locks almost immediately, so this only fires when lock
# detection itself fails; correctness never depends on it.
_HANDSHAKE_TIMEOUT = 10.0


def _make_db(tmp_path: Path) -> Path:
    db_path = TEST_DATABASE_URL
    init_db(db_path)
    with write_transaction(db_path) as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key)"
            " values ('w1', 'ws', 'demo_workflow')"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, source_type, source_id,"
            " title, status, storage_dir)"
            " values ('j1','w1','s','s1','t','running','d')"
        )
        conn.execute(
            "insert into job_nodes(job_id, node_key, status) values ('j1','review','running')"
        )
        materialize_shards(conn, "j1", "review", [{"q": i} for i in range(4)], max_shards=100)
        # Shards 0 and 1 finished in earlier passes; 2 and 3 are the last two.
        for index in (0, 1):
            on_shard_finished(conn, "j1", "review", index, "completed")
    return db_path


def _shard_statuses(db_path: Path) -> list[str]:
    with read_connection(db_path) as conn:
        rows = conn.execute(
            "select status from node_shards where job_id='j1' and node_key='review'"
            " order by shard_index"
        ).fetchall()
    return [str(row["status"]) for row in rows]


def _node_status(db_path: Path) -> str:
    with read_connection(db_path) as conn:
        row = conn.execute(
            "select status from job_nodes where job_id='j1' and node_key='review'"
        ).fetchone()
    return str(row["status"])


def _is_blocked_on_locks(backend_pid: int) -> bool:
    """True when ``backend_pid`` waits on a lock held by another session.

    Scoped to the second finisher's own backend, so concurrent xdist workers
    blocking each other elsewhere cannot produce a false positive.
    """
    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute("select pg_blocking_pids(%s) as blockers", (backend_pid,)).fetchone()
    return bool(row and row["blockers"])


def _run_first_then_second(
    first: Callable[[Any], Any],
    second: Callable[[Any], Any],
) -> Any:
    """Run two finishers with the lost-update interleaving forced, verifiably.

    ``first(conn)`` runs in a thread that signals ``entered`` once its write
    transaction has done its shard work (holding the row locks under the
    fix), then waits for ``release`` to commit. ``second(conn)`` runs in its
    own transaction and is observed by the harness until one of two provable
    states holds:

    * ``second_done`` — the second finisher completed its aggregate read.
      Without the row locks this happens while ``first`` is still open (the
      peer's update is uncommitted), so the stale non-terminal aggregate is
      captured and the assertions fail — the regression is detected, not
      slept past.
    * the second finisher's backend is blocked on ``first``'s locks
      (``pg_blocking_pids``) — the fix working: its read will resume only
      after ``first`` commits, so it observes the terminal state.

    Only then is ``release`` set, letting ``first`` commit and unblocking
    ``second``. A fixed sleep cannot prove either state on a loaded CI host
    (the second thread may not have been scheduled yet), which is why the
    handshake polls for evidence instead.
    """
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    second_done = threading.Event()
    errors: list[BaseException] = []
    second_result: list[Any] = []
    second_pid: list[int] = []

    def first_runner() -> None:
        try:
            with write_transaction(TEST_DATABASE_URL) as conn:
                first(conn)
                entered.set()
                assert release.wait(timeout=10), "first finisher never released"
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    def second_runner() -> None:
        try:
            with write_transaction(TEST_DATABASE_URL) as conn:
                pid = conn.execute("select pg_backend_pid() as pid").fetchone()
                second_pid.append(int(pid["pid"]))
                second_entered.set()
                second_result.append(second(conn))
                second_done.set()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    first_thread = threading.Thread(target=first_runner)
    first_thread.start()
    assert entered.wait(timeout=10), "first finisher never opened its transaction"

    second_thread = threading.Thread(target=second_runner)
    second_thread.start()
    assert second_entered.wait(timeout=10), "second finisher never opened its transaction"

    deadline = time.monotonic() + _HANDSHAKE_TIMEOUT
    while not second_done.is_set():
        if _is_blocked_on_locks(second_pid[0]):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    release.set()

    second_thread.join(timeout=_JOIN_TIMEOUT)
    first_thread.join(timeout=_JOIN_TIMEOUT)
    assert not second_thread.is_alive(), "second finisher hung on shard locks"
    assert not first_thread.is_alive(), "first finisher hung on commit"
    if errors:
        raise errors[0]
    return second_result[0] if second_result else None


def test_second_finisher_observes_peer_commit(tmp_path):
    """The last finisher's aggregate must see the peer's committed shard.

    Without the row locks the second ``on_shard_finished`` reads the pre-peer
    snapshot (shard 2 still 'running') and returns 'running', wedging the
    node; with them it blocks on the peer's commit and returns 'completed'.
    """
    db_path = _make_db(tmp_path)

    def second(conn: Any) -> str:
        return on_shard_finished(conn, "j1", "review", 3, "completed")

    aggregate = _run_first_then_second(
        lambda conn: on_shard_finished(conn, "j1", "review", 2, "completed"),
        second,
    )
    assert aggregate == "completed"
    assert _shard_statuses(db_path) == ["completed"] * 4


def test_second_finisher_mixed_terminal_statuses(tmp_path):
    """Aggregate-semantics invariant (NOT a lost-update detector; see module docstring).

    A completion racing a lease-expiry failure aggregates to 'failed' under
    any interleaving — the failed finisher's own UPDATE decides it — so this
    pins the precedence order (failed beats running) and the shared-path
    behavior, but passes even without the row locks.
    """
    db_path = _make_db(tmp_path)

    def second(conn: Any) -> str:
        return on_shard_finished(conn, "j1", "review", 3, "failed", error_message="lease expired")

    aggregate = _run_first_then_second(
        lambda conn: on_shard_finished(conn, "j1", "review", 2, "completed"),
        second,
    )
    assert aggregate == "failed"
    assert _shard_statuses(db_path) == ["completed", "completed", "completed", "failed"]


def test_finish_shard_execution_concurrent_pair_advances_node(tmp_path):
    """End-to-end lease-finish path: the racing pair advances the node.

    The aggregate is only half the contract — the caller turns the terminal
    aggregate into the ``job_nodes`` update. Drive both finishers through the
    real ``finish_shard_execution`` and assert the node actually completes.
    """
    db_path = _make_db(tmp_path)

    def first(conn: Any) -> None:
        _bind_execution(conn, 2)
        finish_shard_execution(conn, _lease(2), _completed_result(), "2026-09-01T00:00:00Z")

    def second(conn: Any) -> bool:
        _bind_execution(conn, 3)
        return finish_shard_execution(conn, _lease(3), _completed_result(), "2026-09-01T00:00:00Z")

    assert _run_first_then_second(first, second) is True
    assert _shard_statuses(db_path) == ["completed"] * 4
    assert _node_status(db_path) == "completed"


def test_finish_shard_execution_concurrent_mixed_terminal_states(tmp_path):
    """End-to-end mixed-state invariant (NOT a lost-update detector; see module docstring)."""
    db_path = _make_db(tmp_path)

    def first(conn: Any) -> None:
        _bind_execution(conn, 2)
        finish_shard_execution(conn, _lease(2), _completed_result(), "2026-09-01T00:00:00Z")

    def second(conn: Any) -> bool:
        _bind_execution(conn, 3)
        return finish_shard_execution(conn, _lease(3), _failed_result(), "2026-09-01T00:00:00Z")

    assert _run_first_then_second(first, second) is True
    assert _shard_statuses(db_path) == ["completed", "completed", "completed", "failed"]
    assert _node_status(db_path) == "failed"


def _lease(index: int) -> dict[str, str]:
    return {"job_id": "j1", "node_key": "review", "execution_id": f"exec-{index}"}


def _bind_execution(conn: Any, index: int) -> None:
    conn.execute(
        "update node_shards set execution_id=%s"
        " where job_id='j1' and node_key='review' and shard_index=%s",
        (f"exec-{index}", index),
    )


def _completed_result() -> ExecutionResult:
    return ExecutionResult(status="completed", exit_code=0, output_json="{}")


def _failed_result() -> ExecutionResult:
    return ExecutionResult(status="failed", exit_code=1, error_message="lease expired")
