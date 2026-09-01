"""Claim-time advisory lock contract (issue #351).

The workspace capacity domain (`agent-ws:<workspace>`) is agent-only — code
requests have no workspace cap (batch 2 decision 2) — so a kind='code' claim
must not take the `agent-ws` advisory lock: code executes in seconds, claim
rates reach tens per second, and the shared ws lock would serialize code
claims behind concurrent agent claims. These tests pin:

1. a code claim never requests the `agent-ws` lock (pg_locks observed from
   inside the claim transaction, plus a statement-level patch asserting no ws
   lock statement runs at all for a code candidate);
2. the agent claim lock order is unchanged (ws acquired strictly before the
   worker lock);
3. concurrency: a code claim completes while another connection holds the
   workspace's `agent-ws` lock.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import psycopg

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_broker.claim_evaluate import evaluate_candidate
from server.app.agent_broker.claim_scan import SCAN_ROUNDS, ScanState, WorkerView, fetch_candidates
from server.app.agent_control.registry import AgentWorkerRegistry
from server.app.db.transaction import write_transaction
from shared.protocol import CODE_PROTOCOL_VERSION
from tests.helpers.agent_worker_api import (
    enqueue_code as _enqueue_code,
)
from tests.helpers.agent_worker_api import (
    insert_code_job_rows as _insert_code_job_rows,
)
from tests.helpers.agent_worker_api import (
    seed_request as _seed_request,
)
from tests.postgres_support import TEST_DATABASE_URL

# Regression guard rails: the first claim window round the per-kind scan
# uses (claim_scan.SCAN_ROUNDS[0]) and the wire protocol the seeded worker
# declares — imported so a constant bump re-prices these tests instead of
# silently passing against stale magic numbers.
_FIRST_SCAN_WINDOW = SCAN_ROUNDS[0]
_WORKER_PROTOCOL_VERSION = CODE_PROTOCOL_VERSION


def _broker(data_dir) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=data_dir)


def _register_worker(worker_id: str) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        capabilities=["package", "generate"],
        max_concurrency=10,
        max_code_concurrency=5,
        labels={"arch": "arm64"},
        protocol_version=_WORKER_PROTOCOL_VERSION,
    )


def _view(kind: str) -> WorkerView:
    """One claim pass for a single kind: only that pool has headroom."""
    return WorkerView(
        runtimes={"pi"},
        models={("*", "*", "*")},
        labels={"arch": "arm64"},
        allowed_workspaces=set(),
        agent_capacity=10 if kind == "agent" else 0,
        agent_active=0,
        code_capacity=5 if kind == "code" else 0,
        code_active=0,
        protocol_version=_WORKER_PROTOCOL_VERSION,
    )


def _advisory_lock_keys_held(conn: Any) -> set[int]:
    """Advisory lock keys held by a backend (its own session or another's).

    ``pg_advisory_xact_lock(hashtext(key))`` appears in pg_locks as
    (locktype='advisory', classid=0, objid=hashtext(key), objsubid=1).
    Row access is index-based: raw psycopg cursors yield tuples while the
    DatabaseConnection facade yields dicts."""
    rows = conn.execute(
        "select objid from pg_locks"
        " where locktype='advisory' and classid=0 and objsubid=1"
        " and pid = %s",
        (backend_pid(conn),),
    ).fetchall()
    return {int(row["objid"] if isinstance(row, dict) else row[0]) for row in rows}


def backend_pid(conn: Any) -> Any:
    if isinstance(conn, psycopg.Connection):
        return conn.info.backend_pid
    return conn.execute("select pg_backend_pid() as pid").fetchone()["pid"]


def _lock_key(conn: Any, domain: str) -> int:
    row = conn.execute("select hashtext(%s) as k", (domain,)).fetchone()
    value = row["k"] if isinstance(row, dict) else row[0]
    return int(value)


def _claim_candidates(conn: Any, kind: str) -> list[Any]:
    """One bounded scan round exactly as claim_in_transaction runs it."""
    return fetch_candidates(
        conn, per_workspace=_FIRST_SCAN_WINDOW[0], window=_FIRST_SCAN_WINDOW[1], kind=kind
    )


def _patched_execute(conn: Any, on_advisory: Callable[[str, set[int]], None]) -> Callable[..., Any]:
    """Wrap conn.execute so every advisory-lock statement reports its domain
    and the advisory lock set held by the session right after acquiring it."""
    real_execute = conn.execute

    def execute(sql: str, params: Any = None) -> Any:
        cursor = real_execute(sql, params)
        if "pg_advisory_xact_lock" in sql:
            on_advisory(str(params[0]), _advisory_lock_keys_held(conn))
        return cursor

    conn.execute = execute  # type: ignore[method-assign]
    return real_execute


def test_code_claim_transaction_never_holds_agent_ws_lock(job_db) -> None:
    """Acceptance #1: a code claim takes only the agent-worker lock — never
    the workspace's agent-ws lock. Observed from inside the claim transaction
    via pg_locks right after the claim evaluation succeeded."""
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="lock-job-1")
    _enqueue_code(broker, job_id="lock-job-1")
    _register_worker("worker-code")

    with write_transaction(TEST_DATABASE_URL) as conn:
        selected = _claim_candidates(conn, "code")[0]
        claimed = evaluate_candidate(
            broker, conn, "worker-code", selected, _view("code"), ScanState()
        )
        assert claimed is not None
        assert claimed.kind == "code"
        held = _advisory_lock_keys_held(conn)
        assert _lock_key(conn, "agent-worker:worker-code") in held
        assert _lock_key(conn, "agent-ws:test-workspace") not in held


def test_code_claim_never_requests_the_agent_ws_lock(job_db) -> None:
    """Acceptance #1, statement level: patching conn.execute to observe every
    advisory-lock statement a code claim runs, the agent-ws domain never
    appears (the worker domain does — proving the observer works)."""
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="lock-job-2")
    _enqueue_code(broker, job_id="lock-job-2")
    _register_worker("worker-code")

    advisory_domains: list[str] = []

    def on_advisory(domain: str, _held: set[int]) -> None:
        advisory_domains.append(domain)

    with write_transaction(TEST_DATABASE_URL) as conn:
        real_execute = _patched_execute(conn, on_advisory)
        try:
            selected = _claim_candidates(conn, "code")[0]
            claimed = evaluate_candidate(
                broker, conn, "worker-code", selected, _view("code"), ScanState()
            )
        finally:
            conn.execute = real_execute  # type: ignore[method-assign]
        assert claimed is not None
        assert claimed.kind == "code"

    assert advisory_domains == ["agent-worker:worker-code"]


def test_agent_claim_lock_order_is_ws_then_worker(job_db) -> None:
    """Acceptance #1: the agent claim lock order is unchanged — the agent-ws
    lock is acquired strictly before the agent-worker lock. After the first
    advisory statement (ws) the worker key is not yet held; after the second
    both are."""
    broker = _broker(job_db.jobs_dir.parent)
    _seed_request(job_db, job_id="order-job", node_key="generate", limit=20)
    _register_worker("worker-order")

    snapshots: list[tuple[str, set[int]]] = []

    def on_advisory(domain: str, held: set[int]) -> None:
        snapshots.append((domain, set(held)))

    with write_transaction(TEST_DATABASE_URL) as conn:
        real_execute = _patched_execute(conn, on_advisory)
        try:
            selected = _claim_candidates(conn, "agent")[0]
            claimed = evaluate_candidate(
                broker, conn, "worker-order", selected, _view("agent"), ScanState()
            )
        finally:
            conn.execute = real_execute  # type: ignore[method-assign]
        assert claimed is not None
        assert claimed.kind == "agent"
        ws_key = _lock_key(conn, "agent-ws:test-workspace")
        worker_key = _lock_key(conn, "agent-worker:worker-order")

    domains = [domain for domain, _ in snapshots]
    assert domains == ["agent-ws:test-workspace", "agent-worker:worker-order"]
    # First statement acquired the ws lock but NOT the worker lock yet.
    _, held_after_ws = snapshots[0]
    assert ws_key in held_after_ws
    assert worker_key not in held_after_ws
    # Second statement acquired the worker lock with the ws lock still held.
    _, held_after_worker = snapshots[1]
    assert ws_key in held_after_worker
    assert worker_key in held_after_worker


def test_code_claim_completes_while_agent_ws_lock_held(job_db) -> None:
    """Acceptance #3: a code claim must not queue behind a concurrent agent
    claim holding the workspace lock. One connection holds
    `agent-ws:test-workspace` for the whole assertion (as an in-flight agent
    claim would); the code claim on another connection still completes."""
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="lock-job-3")
    _enqueue_code(broker, job_id="lock-job-3")
    _register_worker("worker-code")

    holder = psycopg.connect(TEST_DATABASE_URL)
    try:
        holder.execute("select pg_advisory_xact_lock(hashtext(%s))", ("agent-ws:test-workspace",))
        assert _lock_key(holder, "agent-ws:test-workspace") in _advisory_lock_keys_held(holder)

        # Run the claim in a thread with a hard join timeout: if a regression
        # reintroduces the ws lock for code claims, broker.claim() would block
        # on the holder's advisory lock forever (no pytest-timeout in this
        # repo) — the join timeout turns that hang into a fast, diagnosable
        # failure instead of a wedged postgres gate.
        claim_result: dict[str, object] = {}

        def run_claim() -> None:
            claim_result["claim"] = broker.claim("worker-code")

        claim_thread = threading.Thread(target=run_claim)
        claim_thread.start()
        claim_thread.join(timeout=30)
        assert not claim_thread.is_alive(), (
            "code claim blocked behind the agent-ws advisory lock — the #351 fix regressed"
        )
        claimed = claim_result["claim"]

        assert claimed is not None
        assert claimed.kind == "code"
        with job_db._connect_read() as conn:
            row = conn.execute(
                "select state, worker_id from agent_execution_requests where execution_id=%s",
                (claimed.execution_id,),
            ).fetchone()
        assert row["state"] == "claimed"
        assert row["worker_id"] == "worker-code"
    finally:
        holder.rollback()
        holder.close()
