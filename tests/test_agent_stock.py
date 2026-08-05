"""Agent stockpile target math, snapshot loading, and the allows() gate."""

from __future__ import annotations

from server.app.workflow_worker.agent_stock import (
    AgentStockConfig,
    StockBucket,
    StockSnapshot,
    load_stock_snapshot,
)
from tests.postgres_support import TEST_DATABASE_URL


def test_target_from_completion_rate() -> None:
    config = AgentStockConfig()  # window 1800s, horizon 300s
    assert StockBucket(done_in_window=60).target(config) == 10
    assert StockBucket(done_in_window=1).target(config) == 4  # ceil, then min_stock floor


def test_target_floored_by_claimed_plus_min_stock() -> None:
    config = AgentStockConfig(min_stock=4)
    assert StockBucket(claimed=3).target(config) == 7


def test_target_min_stock_floor_and_max_cap() -> None:
    config = AgentStockConfig(min_stock=4, max_stock=500)
    assert StockBucket().target(config) == 4
    assert StockBucket(done_in_window=999_999).target(config) == 500


def test_allows_compares_queued_against_target() -> None:
    config = AgentStockConfig(min_stock=2, max_stock=10)
    snapshot = StockSnapshot(
        config=config,
        buckets={("ws1", "agent-x"): StockBucket(queued=2)},
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
    finished_at: str | None = None,
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('ws1', 'Test') on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, 'ws1', 'questions', 'question', %s) on conflict(id) do nothing",
            (job_id, job_id),
        )
        conn.execute(
            "insert into agent_execution_requests(execution_id, workspace_id, job_id,"
            " workflow_key, node_key, agent_id, agent_definition_hash,"
            " node_concurrency_limit, queued_at, manifest_json, state, finished_at)"
            " values (%s, 'ws1', %s, 'questions', 'n1', %s, 'hash', 1,"
            " current_timestamp, '{}', %s,"
            + (finished_at if finished_at is not None else "null")
            + ")",
            (execution_id, job_id, agent_id, state),
        )


def test_load_stock_snapshot_counts_states_and_window(job_db) -> None:
    _insert_request(job_db, execution_id="q1", job_id="j1", agent_id="agent-x", state="queued")
    _insert_request(job_db, execution_id="q2", job_id="j2", agent_id="agent-x", state="queued")
    _insert_request(job_db, execution_id="c1", job_id="j3", agent_id="agent-x", state="claimed")
    # 'reporting' is neither stock nor in-flight execution.
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

    bucket = snapshot.buckets[("ws1", "agent-x")]
    assert bucket.queued == 2
    assert bucket.claimed == 1
    assert bucket.done_in_window == 1  # only the in-window done row
    assert snapshot.buckets[("ws1", "agent-y")].queued == 1


def test_load_stock_snapshot_empty_allows_everything(job_db) -> None:
    snapshot = load_stock_snapshot(TEST_DATABASE_URL, AgentStockConfig())
    assert snapshot.allows("ws1", "agent-x") is True
