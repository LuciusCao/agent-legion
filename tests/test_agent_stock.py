"""Agent stockpile target math, snapshot loading, and the allows() gate."""

from __future__ import annotations

import json

from server.app.workflow_worker.agent_stock import (
    AgentStockConfig,
    StockBucket,
    StockSnapshot,
)
from server.app.workflow_worker.agent_stock_snapshot import TIER_ROWS_SQL, load_stock_snapshot
from tests.postgres_support import TEST_DATABASE_URL

WS_AGENT = ("ws1", "agent-x")


def _snapshot(config: AgentStockConfig, bucket: StockBucket, capacity: int = 0) -> StockSnapshot:
    return StockSnapshot(config=config, capacity=capacity, buckets={WS_AGENT: bucket})


def _target(config: AgentStockConfig, bucket: StockBucket, capacity: int = 0) -> int:
    return _snapshot(config, bucket, capacity).target(WS_AGENT)


def test_target_from_completion_rate() -> None:
    config = AgentStockConfig()  # window 1800s, horizon 180s
    assert _target(config, StockBucket(done_in_window=60)) == 6
    assert _target(config, StockBucket(done_in_window=1)) == 4  # ceil, then min_stock floor


def test_target_floored_by_worker_capacity() -> None:
    config = AgentStockConfig(min_stock=4)
    # Cold start: no completions yet, but the live fleet must find stock.
    assert _target(config, StockBucket(), capacity=128) == 128
    # The rate amplifier raises the target beyond the floor for fast tasks.
    assert _target(config, StockBucket(done_in_window=2400), capacity=128) == 240
    # And sinks below it for slow ones.
    assert _target(config, StockBucket(done_in_window=240), capacity=128) == 128


def test_target_min_stock_floor_and_max_cap() -> None:
    config = AgentStockConfig(min_stock=4, max_stock=500)
    assert _target(config, StockBucket()) == 4
    assert _target(config, StockBucket(done_in_window=999_999)) == 500


def test_capacity_pool_deeper_tier_draws_first() -> None:
    """The fleet capacity is one shared pool, drawn in workflow order."""
    config = AgentStockConfig(min_stock=4)
    snapshot = StockSnapshot(
        config=config,
        capacity=128,
        buckets={
            ("ws1", "upstream"): StockBucket(queued=0),
            ("ws1", "downstream"): StockBucket(queued=100),
        },
        priorities={("ws1", "upstream"): 1, ("ws1", "downstream"): 2},
    )
    # The deeper bucket sees the whole pool (only shallower queued deducted).
    assert snapshot.target(("ws1", "downstream")) == 128
    # The shallower bucket only sees what deeper work leaves behind.
    assert snapshot.target(("ws1", "upstream")) == 28
    # Fleet-wide stock stays bounded by capacity: 100 queued + 28 headroom.
    assert snapshot.allows("ws1", "upstream", extra=27) is True
    assert snapshot.allows("ws1", "upstream", extra=28) is False


def test_capacity_pool_same_tier_buckets_deduct_each_other() -> None:
    config = AgentStockConfig(min_stock=4)
    snapshot = StockSnapshot(
        config=config,
        capacity=128,
        buckets={
            ("ws1", "branch-a"): StockBucket(queued=60),
            ("ws1", "branch-b"): StockBucket(queued=0),
        },
        priorities={("ws1", "branch-a"): 3, ("ws1", "branch-b"): 3},
    )
    # Fan-out siblings share the remaining pool instead of copying it.
    assert snapshot.target(("ws1", "branch-a")) == 128
    assert snapshot.target(("ws1", "branch-b")) == 68


def test_capacity_pool_unknown_pair_draws_last() -> None:
    config = AgentStockConfig(min_stock=4)
    snapshot = StockSnapshot(
        config=config,
        capacity=128,
        buckets={("ws1", "known"): StockBucket(queued=90)},
        priorities={("ws1", "known"): 0},
    )
    # A pair with no workflow-tier evidence ranks below every known bucket:
    # its floor is the pool minus ALL known queued stock.
    assert snapshot.target(("ws1", "mystery")) == 38
    assert snapshot.allows("ws1", "mystery", extra=38) is False
    # With an empty pool the unknown pair still gets the min_stock floor.
    drained = StockSnapshot(
        config=config,
        capacity=128,
        buckets={("ws1", "known"): StockBucket(queued=500)},
        priorities={("ws1", "known"): 0},
    )
    assert drained.target(("ws1", "mystery")) == 4


def test_allows_compares_queued_against_target() -> None:
    config = AgentStockConfig(min_stock=2, max_stock=10)
    snapshot = StockSnapshot(
        config=config,
        buckets={WS_AGENT: StockBucket(queued=2)},
    )
    assert snapshot.allows("ws1", "agent-x") is False
    # Unknown pairs start with empty buckets and are always allowed.
    assert snapshot.allows("ws2", "agent-x") is True


def _insert_request(
    job_db,
    *,
    execution_id: str,
    job_id: str,
    agent_id: str,
    state: str,
    node_key: str = "n1",
    revision_id: str | None = None,
    finished_at: str | None = None,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws1', 'Test', 'demo_workflow') on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id,"
            " workflow_revision_id)"
            " values (%s, 'ws1', 'questions', 'question', %s, %s) on conflict(id) do nothing",
            (job_id, job_id, revision_id or ""),
        )
        conn.execute(
            "insert into agent_execution_requests(execution_id, workspace_id, job_id,"
            " workflow_key, node_key, agent_id, agent_definition_hash,"
            " node_concurrency_limit, queued_at, manifest_json, state, finished_at)"
            " values (%s, 'ws1', %s, 'questions', %s, %s, 'hash', 1,"
            " current_timestamp, '{}', %s,"
            + (finished_at if finished_at is not None else "null")
            + ")",
            (execution_id, job_id, node_key, agent_id, state),
        )


def test_load_stock_snapshot_counts_states_and_window(job_db) -> None:
    _insert_request(job_db, execution_id="q1", job_id="j1", agent_id="agent-x", state="queued")
    _insert_request(job_db, execution_id="q2", job_id="j2", agent_id="agent-x", state="queued")
    # 'claimed' and 'reporting' are in-flight work, not stock.
    _insert_request(job_db, execution_id="c1", job_id="j3", agent_id="agent-x", state="claimed")
    _insert_request(job_db, execution_id="r1", job_id="j4", agent_id="agent-x", state="reporting")
    _insert_request(
        job_db,
        execution_id="d1",
        job_id="j5",
        agent_id="agent-x",
        state="done",
        finished_at="current_timestamp",
    )
    _insert_request(
        job_db,
        execution_id="d2",
        job_id="j6",
        agent_id="agent-x",
        state="done",
        finished_at="current_timestamp - interval '2 hours'",
    )
    _insert_request(
        job_db,
        execution_id="x1",
        job_id="j7",
        agent_id="agent-x",
        state="cancelled",
        finished_at="current_timestamp",
    )
    _insert_request(job_db, execution_id="y1", job_id="j8", agent_id="agent-y", state="queued")

    snapshot = load_stock_snapshot(TEST_DATABASE_URL, AgentStockConfig())

    bucket = snapshot.buckets[WS_AGENT]
    assert bucket.queued == 2
    assert bucket.done_in_window == 1  # only the in-window done row
    assert snapshot.buckets[("ws1", "agent-y")].queued == 1
    assert snapshot.capacity == 0  # no registered workers


def _insert_revision(job_db, *, revision_id: str, nodes: dict, edges: list) -> None:
    definition_json = json.dumps(
        {"key": "questions", "label": "Questions", "nodes": nodes, "edges": edges}
    )
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('ws1', 'Test', 'demo_workflow') on conflict(id) do nothing"
        )
        conn.execute(
            "insert into workflow_revisions(id, workspace_id, workflow_key, version,"
            " status, definition_json, definition_hash)"
            " values (%s, 'ws1', 'questions', 1, 'active', %s, 'hash')",
            (revision_id, definition_json),
        )


def test_load_stock_snapshot_derives_priorities_from_workflow_dag(job_db) -> None:
    _insert_revision(
        job_db,
        revision_id="rev-1",
        nodes={
            "n1": {"capability": "generate"},
            "n2": {"capability": "review", "after": ["n1"]},
            "n3": {"capability": "assess", "after": ["n2"]},
        },
        edges=[],
    )
    _insert_request(
        job_db,
        execution_id="q1",
        job_id="j1",
        agent_id="agent-gen",
        state="queued",
        node_key="n1",
        revision_id="rev-1",
    )
    _insert_request(
        job_db,
        execution_id="q2",
        job_id="j2",
        agent_id="agent-assess",
        state="queued",
        node_key="n3",
        revision_id="rev-1",
    )
    # Requests whose job has no revision snapshot stay in the unknown tier.
    _insert_request(job_db, execution_id="q3", job_id="j3", agent_id="agent-y", state="queued")

    snapshot = load_stock_snapshot(TEST_DATABASE_URL, AgentStockConfig())
    # capacity is DB-derived; rebuild with an explicit pool for the assertion.
    snapshot = StockSnapshot(
        config=snapshot.config,
        capacity=128,
        buckets=snapshot.buckets,
        priorities=snapshot.priorities,
    )

    assert snapshot.priorities[("ws1", "agent-gen")] == 0
    assert snapshot.priorities[("ws1", "agent-assess")] == 2
    assert ("ws1", "agent-y") not in snapshot.priorities
    # Deeper node draws the full pool; the shallow one only sees leftovers.
    assert snapshot.target(("ws1", "agent-assess")) == 128
    assert snapshot.target(("ws1", "agent-gen")) == 127  # 128 - 1 queued downstream


def _insert_worker(
    job_db,
    *,
    worker_id: str,
    max_concurrency: int,
    last_seen_at: str = "current_timestamp",
    revoked: bool = False,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into agent_workers(worker_id, runtimes_json, max_concurrency,"
            " protocol_version, token_hash, registered_at, last_seen_at, revoked_at)"
            " values (%s, '[\"pi\"]', %s, 1, 'hash', current_timestamp, "
            + last_seen_at
            + ", "
            + ("current_timestamp" if revoked else "null")
            + ")",
            (worker_id, max_concurrency),
        )


def test_load_stock_snapshot_capacity_counts_only_live_workers(job_db) -> None:
    _insert_worker(job_db, worker_id="w-live", max_concurrency=128)
    _insert_worker(
        job_db,
        worker_id="w-stale",
        max_concurrency=64,
        last_seen_at="current_timestamp - interval '1 hour'",
    )
    _insert_worker(job_db, worker_id="w-revoked", max_concurrency=32, revoked=True)

    snapshot = load_stock_snapshot(TEST_DATABASE_URL, AgentStockConfig())

    assert snapshot.capacity == 128
    assert snapshot.target(WS_AGENT) == 128  # capacity floor, no history needed


def test_load_stock_snapshot_empty_allows_everything(job_db) -> None:
    snapshot = load_stock_snapshot(TEST_DATABASE_URL, AgentStockConfig())
    assert snapshot.allows("ws1", "agent-x") is True


def test_tier_rows_query_never_seq_scans(job_db) -> None:
    """Pin the performance property: the tier query must stay index driven.
    The old `state='queued' or finished_at > ...` predicate defeated every
    partial index and seq-scanned the whole agent_execution_requests table
    on every stock pass (production: 630k rows, once per workflow pass)."""
    with job_db.connect() as conn:
        rows = conn.execute(f"explain {TIER_ROWS_SQL}", (1800, 1800)).fetchall()

    plan = "\n".join(str(row[0]) for row in rows)
    assert "Seq Scan on agent_execution_requests" not in plan
