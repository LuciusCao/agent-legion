"""Schema v88 regression: the job NODE counter deadlock ring (#690).

v82 (#659, tests/db/test_status_counts_deadlock.py) removed the cross-
transaction counter-row ring from the job-level families. The node-level
sibling kept the v56 shape: a FOR EACH ROW trigger on job_nodes upserting the
shared (workspace_id, node_key, status) row once per changed node, old
status first, new status second. Two transactions in one workspace whose
node transitions START on different counter rows take them in business
order and close an AB-BA ring — the claim-promote × result-commit pair
below. Every transaction queued behind either side inherits the outage: the
heartbeat-batch arm pins the three-party shape #690 observed, where the
batched heartbeat is the victim without ever touching a counter row itself.

Both rings are reproduced against the pre-v88 bump body in the control arm
(``test_pre_v88_shape_reproduces_both_rings``, which swaps that body in under
a fresh schema), so the no-deadlock assertions are proven to bite. Ordering
is deterministic, not sleep-based: each racing step waits until the other
backend is observably blocked on a lock (pg_stat_activity) or has finished.
A low ``deadlock_timeout`` makes a reintroduced ring fail in milliseconds;
``lock_timeout`` bounds any unexpected wait.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable

import psycopg
import pytest

from server.app.db.rows import string_dict_row
from tests.helpers.pre_v88_node_counts import PRE_V88_BUMP_SQL
from tests.postgres_support import TEST_DATABASE_URL

_TIMEOUTS = ("set deadlock_timeout='50ms'", "set lock_timeout='5s'")
_NODE = "review"


def _connect() -> psycopg.Connection:
    conn = psycopg.connect(TEST_DATABASE_URL, autocommit=False, row_factory=string_dict_row)
    for timeout in _TIMEOUTS:
        conn.execute(timeout)
    return conn


def _classify(exc: psycopg.Error) -> str:
    sqlstate = getattr(exc, "sqlstate", None)
    if sqlstate == "40P01":
        return "deadlock"
    if sqlstate == "55P03":
        return "lock_timeout"
    return f"{type(exc).__name__}:{sqlstate}"


class _Party:
    """One transaction driven step by step: the main thread hands it one
    statement at a time and waits until that statement either finished or
    is observably blocked on a lock, so the interleave is exact."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.failure: str | None = None
        self._conn = _connect()
        self.pid = int(self._conn.info.backend_pid)
        self._thread: threading.Thread | None = None

    def run(self, statements: list[tuple[str, tuple[str, ...]]], *, commit: bool) -> None:
        """Run statements on a worker thread and return once it finished or
        blocked (the thread keeps running and resumes when unblocked)."""
        assert self._thread is None or not self._thread.is_alive(), self.name

        def _body() -> None:
            if self.failure is not None:
                return
            try:
                for sql, params in statements:
                    self._conn.execute(sql, params)
                if commit:
                    self._conn.commit()
            except psycopg.Error as exc:
                self.failure = _classify(exc)
                with contextlib.suppress(psycopg.Error):
                    self._conn.rollback()

        self._thread = threading.Thread(target=_body, name=f"tx-{self.name}")
        self._thread.start()
        _wait_blocked_or_done(self.pid, self._thread)

    def join(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=30)
            assert not self._thread.is_alive(), f"{self.name} never resolved"
        self._conn.close()


def _wait_blocked_or_done(pid: int, thread: threading.Thread, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as mon:
        while time.monotonic() < deadline:
            if not thread.is_alive():
                return
            row = mon.execute(
                "select wait_event_type from pg_stat_activity where pid=%s", (pid,)
            ).fetchone()
            if row is not None and row["wait_event_type"] == "Lock":
                return
            time.sleep(0.005)
    raise AssertionError(f"backend {pid} neither blocked nor finished within {timeout}s")


def _seed(workspace: str, nodes: dict[str, str], requests: tuple[str, ...] = ()) -> None:
    """One job per node id (all on node_key ``review``), status as given."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as seed:
        seed.execute(
            "insert into workspaces(id, name) values (%s, %s)",
            (workspace, workspace),
        )
        for job_id, status in nodes.items():
            seed.execute(
                "insert into jobs(id, workspace_id, source_type, source_id, status)"
                " values (%s, %s, 'test', %s, 'running')",
                (job_id, workspace, job_id),
            )
            seed.execute(
                "insert into job_nodes(job_id, node_key, status) values (%s, %s, %s)",
                (job_id, _NODE, status),
            )
        # One active request per (job, node): request i rides job i.
        for execution_id, job_id in zip(requests, nodes, strict=False):
            seed.execute(
                "insert into agent_execution_requests(execution_id, workspace_id, job_id,"
                " node_key, agent_id, agent_definition_hash, node_concurrency_limit,"
                " state, queued_at, manifest_json)"
                " values (%s, %s, %s, %s, 'agent', 'hash', 1, 'claimed',"
                " current_timestamp, '{}')",
                (execution_id, workspace, job_id, _NODE),
            )


def _set_node(job_id: str, status: str) -> tuple[str, tuple[str, ...]]:
    return (
        "update job_nodes set status=%s where job_id=%s and node_key=%s",
        (status, job_id, _NODE),
    )


def _node_counts(workspace: str) -> dict[str, int]:
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as conn:
        rows = conn.execute(
            "select status, sum(cnt) as cnt from ("
            " select status, cnt from workspace_job_node_status_counts"
            " where workspace_id=%s and node_key=%s"
            " union all"
            " select status, delta from workspace_job_node_status_count_deltas"
            " where workspace_id=%s and node_key=%s"
            ") c group by status having sum(cnt)<>0",
            (workspace, _NODE, workspace, _NODE),
        ).fetchall()
    return {str(row["status"]): int(row["cnt"]) for row in rows}


def _node_group_by(workspace: str) -> dict[str, int]:
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as conn:
        rows = conn.execute(
            "select jn.status, count(*) as cnt from job_nodes jn join jobs j on j.id = jn.job_id"
            " where j.workspace_id=%s and jn.node_key=%s group by 1",
            (workspace, _NODE),
        ).fetchall()
    return {str(row["status"]): int(row["cnt"]) for row in rows}


def _claim_vs_result_ring(workspace: str) -> list[str | None]:
    """Two-party ring: a claim promoting two nodes vs a result-commit wave.

    claim stmt1 (n1 pending->running) takes (pending, running); result stmt1
    (n2 queued->completed) takes (queued, completed) — disjoint starts;
    result stmt2 (n3 pending->completed) wants claim's pending row; claim
    stmt2 (n4 queued->running) wants result's queued row: AB-BA on the
    pre-v88 shape. v88: the result side loses the class-88 try-lock and
    only appends deltas, so it commits before claim's second statement.
    """
    p = workspace
    _seed(
        workspace,
        {f"{p}-n1": "pending", f"{p}-n2": "queued", f"{p}-n3": "pending", f"{p}-n4": "queued"},
    )
    claim, result = _Party("claim"), _Party("result")
    try:
        claim.run([_set_node(f"{p}-n1", "running")], commit=False)
        result.run(
            [_set_node(f"{p}-n2", "completed"), _set_node(f"{p}-n3", "completed")], commit=True
        )
        claim.run([_set_node(f"{p}-n4", "running")], commit=True)
    finally:
        claim.join()
        result.join()
    return [claim.failure, result.failure]


def _claim_heartbeat_finish_ring(workspace: str) -> list[str | None]:
    """Three-party ring with the batched heartbeat as the bystander victim.

    finish stmt1 flips n1 running->completed (holds the running/completed
    counter rows); claim marks request e2 claimed then promotes n2
    queued->running (pre-v88: waits on finish's running row); the heartbeat
    batch locks e1 then e2 FOR UPDATE (waits on claim); finish stmt2 reports
    e1 (waits on the heartbeat): finish -> heartbeat -> claim -> finish. The
    heartbeat never touches a counter row, yet on the old shape it is part
    of the cycle. v88 removes the claim -> finish counter edge.
    """
    p = workspace
    _seed(
        workspace,
        {f"{p}-n1": "running", f"{p}-n2": "queued"},
        requests=(f"{p}-e1", f"{p}-e2"),
    )
    finish, claim, beat = _Party("finish"), _Party("claim"), _Party("heartbeat")
    lock_request = (
        "select execution_id from agent_execution_requests where execution_id=%s for update"
    )
    try:
        finish.run([_set_node(f"{p}-n1", "completed")], commit=False)
        claim.run(
            [
                (
                    "update agent_execution_requests set claimed_at=current_timestamp"
                    " where execution_id=%s",
                    (f"{p}-e2",),
                ),
                _set_node(f"{p}-n2", "running"),
            ],
            commit=True,
        )
        beat.run([(lock_request, (f"{p}-e1",)), (lock_request, (f"{p}-e2",))], commit=True)
        finish.run(
            [
                (
                    "update agent_execution_requests set state='reporting' where execution_id=%s",
                    (f"{p}-e1",),
                )
            ],
            commit=True,
        )
    finally:
        finish.join()
        claim.join()
        beat.join()
    return [finish.failure, claim.failure, beat.failure]


_RINGS: dict[str, Callable[[str], list[str | None]]] = {
    "claim_vs_result": _claim_vs_result_ring,
    "claim_heartbeat_finish": _claim_heartbeat_finish_ring,
}


@pytest.mark.postgres
@pytest.mark.parametrize("ring", sorted(_RINGS))
def test_node_counter_rings_have_no_waiting_edge(ring: str) -> None:
    workspace = f"nsc88-{ring.replace('_', '-')}"
    failures = _RINGS[ring](workspace)
    assert failures == [None] * len(failures), failures
    assert _node_counts(workspace) == _node_group_by(workspace)


@pytest.mark.postgres
@pytest.mark.fresh_schema
def test_pre_v88_shape_reproduces_both_rings() -> None:
    """Control arm: with the v87 bump body swapped back in, both
    interleaves above close a real ring (exactly one victim each) — the
    no-deadlock pins are not vacuous. fresh_schema rebuilds afterwards."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(PRE_V88_BUMP_SQL)
    for ring, scenario in sorted(_RINGS.items()):
        failures = scenario(f"nsc87-{ring.replace('_', '-')}")
        assert sorted(f for f in failures if f is not None) == ["deadlock"], (ring, failures)


@pytest.mark.postgres
def test_try_lock_loser_commits_exact_pending_deltas(tmp_path) -> None:
    """A loser commits without waiting; the reader stays exact while the
    winner is still open, and the next winner drains the committed tail."""
    from server.app.jobs import JobQueries

    workspace = "nsc88-pending"
    _seed(workspace, {"nsc88-pending-a": "pending", "nsc88-pending-b": "pending"})
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    winner, loser = _Party("winner"), _Party("loser")
    try:
        winner.run([_set_node("nsc88-pending-a", "running")], commit=False)
        loser.run([_set_node("nsc88-pending-b", "completed")], commit=True)
        loser.join()
        assert loser.failure is None, loser.failure
        # The winner is uncommitted: the snapshot sees the loser's committed
        # node row and its pending delta, none of the winner's changes.
        assert queries.count_workspace_job_nodes_by_status(workspace, workspace) == {
            _NODE: _node_group_by(workspace)
        }
        winner.run([], commit=True)
    finally:
        winner.join()
    assert queries.count_workspace_job_nodes_by_status(workspace, workspace) == {
        _NODE: _node_group_by(workspace)
    }
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id='nsc88-pending-a'"
            " and node_key=%s",
            (_NODE,),
        )
        pending = conn.execute(
            "select count(*) as n from workspace_job_node_status_count_deltas"
            " where workspace_id=%s",
            (workspace,),
        ).fetchone()
    assert int(pending["n"]) == 0
    assert _node_counts(workspace) == _node_group_by(workspace) == {"completed": 2}


@pytest.mark.postgres
def test_job_and_workspace_delete_with_pending_deltas() -> None:
    """The deduct trigger routes through the same entry (a loser job delete
    appends negative deltas), and a workspace cascade with pending deltas
    still succeeds — the deduct arm skips the vanished parent."""
    workspace = "nsc88-delete"
    _seed(
        workspace,
        {"nsc88-delete-a": "running", "nsc88-delete-b": "completed", "nsc88-delete-c": "queued"},
    )
    winner, deleter = _Party("winner"), _Party("deleter")
    try:
        winner.run([_set_node("nsc88-delete-a", "completed")], commit=False)
        deleter.run([("delete from jobs where id=%s", ("nsc88-delete-b",))], commit=True)
        deleter.join()
        assert deleter.failure is None, deleter.failure
        winner.run([], commit=True)
    finally:
        winner.join()
    assert _node_counts(workspace) == _node_group_by(workspace) == {"completed": 1, "queued": 1}
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as conn:
        conn.execute("delete from workspaces where id=%s", (workspace,))
        leftover = conn.execute(
            "select (select count(*) from workspace_job_node_status_counts where workspace_id=%s)"
            " + (select count(*) from workspace_job_node_status_count_deltas"
            " where workspace_id=%s) as n",
            (workspace, workspace),
        ).fetchone()
    assert int(leftover["n"]) == 0


@pytest.mark.no_db
def test_schema_file_replay_cannot_restore_blocking_bump() -> None:
    """The schema file replays on every upgrade while v88 runs once: the
    bump entry must live only in the migration SQL, never in the file."""
    from pathlib import Path

    db_dir = Path(__file__).resolve().parents[2] / "server/app/db"
    schema_text = (db_dir / "postgres_schema.sql").read_text(encoding="utf-8")
    migration_text = (db_dir / "migrations/job_node_status_count_deltas.sql").read_text(
        encoding="utf-8"
    )
    assert "function bump_job_node_status_counts" not in schema_text
    assert "create or replace function bump_job_node_status_counts(" in migration_text


@pytest.mark.postgres
def test_node_counter_family_never_blocks_on_advisory_locks() -> None:
    """Shape pin: the single write entry try-locks class 88 and appends on
    loss; no function in the family takes a blocking advisory lock, and the
    schema-file trigger functions write only through that entry."""
    names = (
        "bump_job_node_status_counts",
        "try_fold_job_node_status_counts",
        "apply_job_node_status_count",
        "sync_job_node_status_counts",
        "deduct_job_node_status_counts",
        "rekey_job_node_status_counts",
    )
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=string_dict_row) as conn:
        rows = conn.execute(
            "select proname, prosrc from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
            " where n.nspname = current_schema() and proname = any(%s)"
            " and pg_get_function_identity_arguments(p.oid) not like '%%workflow_key%%'",
            (list(names),),
        ).fetchall()
    by_name = {str(row["proname"]): str(row["prosrc"]) for row in rows}
    assert set(by_name) == set(names)
    assert (
        "pg_try_advisory_xact_lock(88, hashtext('ws:' || k))"
        in by_name["try_fold_job_node_status_counts"]
    )
    assert "returning node_key, status, delta" in by_name["try_fold_job_node_status_counts"]
    assert (
        "insert into workspace_job_node_status_count_deltas"
        in by_name["bump_job_node_status_counts"]
    )
    assert "pg_advisory_xact_lock" not in "".join(by_name.values())
    for trigger_fn in names[3:]:
        assert "workspace_job_node_status_counts" not in by_name[trigger_fn], trigger_fn
