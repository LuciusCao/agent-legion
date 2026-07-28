"""Concurrency contract tests for the Agent broker claim path (spec §Testing).

Covers the review-required scenarios the sequential tests in
``tests/test_agent_broker.py`` cannot reach: the 19/20 two-worker claim race,
late-result/zombie-heartbeat rejection after requeue, bounded cross-workspace
fairness, the requeue guard against just-completed nodes, job-control
re-checks, and stale-definition reaping.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_catalog import AgentDefinition, sync_agent_definitions
from server.app.agent_workers import AgentWorkerRegistry
from tests.postgres_support import TEST_DATABASE_URL


def _definition(**overrides) -> AgentDefinition:
    values = {
        "capability": "generate",
        "runtime": "pi",
        "skill": "question/generate",
        "requires_labels": {"arch": "arm64"},
    }
    values.update(overrides)
    return AgentDefinition(**values)


def _seed_request(
    job_db,
    *,
    job_id: str,
    workspace_id: str = "test-workspace",
    node_key: str = "generate",
    workspace_cap: int | None = 20,
) -> str:
    definition = _definition()
    sync_agent_definitions(TEST_DATABASE_URL, {"generator-v1": definition})
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (?, ?) on conflict(id) do nothing",
            (workspace_id, workspace_id),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (?, ?, 'questions', 'question', ?)",
            (job_id, workspace_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (?, ?)", (job_id, node_key))
        conn.execute(
            "insert into workspace_node_routes(workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values (?, 'questions', ?, 'agent', 'generator-v1')"
            " on conflict(workspace_id, workflow_key, node_key) do nothing",
            (workspace_id, node_key),
        )
        if workspace_cap is not None:
            conn.execute(
                "insert into workspace_agent_capacities(workspace_id, max_concurrency)"
                " values (?, ?)"
                " on conflict(workspace_id) do update"
                " set max_concurrency=excluded.max_concurrency",
                (workspace_id, workspace_cap),
            )
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    execution_id = broker.enqueue(
        AgentExecutionRequest(
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key="questions",
            node_key=node_key,
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            manifest={
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "pi": {"provider": "gateway", "model": "test-model"},
            },
        )
    )
    assert execution_id is not None
    return execution_id


def _register(registry: AgentWorkerRegistry, worker_id: str, *, max_concurrency: int = 10) -> None:
    registry.issue_token(
        worker_id=worker_id,
        name=worker_id,
        runtimes=["pi"],
        max_concurrency=max_concurrency,
        labels={"arch": "arm64"},
    )


def _claimed_count(job_db) -> int:
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select count(*) as cnt from agent_execution_requests where state='claimed'"
        ).fetchone()
    return int(row["cnt"])


def test_two_workers_racing_for_last_node_slot_exactly_one_wins(job_db) -> None:
    """Workspace at 19/20: two concurrent claims must serialize on the workspace domain."""
    for index in range(25):
        _seed_request(job_db, job_id=f"race-job-{index}", workspace_cap=20)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    for worker_id in ("worker-1", "worker-2", "worker-3"):
        _register(registry, worker_id)
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    for _ in range(10):
        assert broker.claim("worker-1") is not None
    for _ in range(9):
        assert broker.claim("worker-2") is not None
    assert _claimed_count(job_db) == 19

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def race(worker_id: str) -> None:
        barrier.wait(timeout=10)
        results[worker_id] = AgentExecutionBroker(TEST_DATABASE_URL).claim(worker_id)

    threads = [threading.Thread(target=race, args=(w,)) for w in ("worker-2", "worker-3")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    winners = [claim for claim in results.values() if claim is not None]
    assert len(winners) == 1
    assert _claimed_count(job_db) == 20


def test_late_result_and_zombie_heartbeat_rejected_after_requeue(job_db) -> None:
    execution_id = _seed_request(job_db, job_id="late-job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register(registry, "worker-1")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, lease_ttl_seconds=1)
    first = broker.claim("worker-1")
    assert first is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=? where execution_id=?",
            (datetime.now(UTC) - timedelta(seconds=10), execution_id),
        )
    assert broker.sweep_expired_claims() == [execution_id]

    # Late result / heartbeat from the invalidated attempt are rejected.
    assert (
        broker.mark_done(execution_id, "worker-1", first.lease_id, {"status": "completed"}) is None
    )
    assert broker.heartbeat(execution_id, "worker-1", first.lease_id) is False

    # The SAME worker re-claims (new lease): zombie first-attempt process must
    # not renew or finish the new lease.
    second = broker.claim("worker-1")
    assert second is not None
    assert second.lease_id != first.lease_id
    assert broker.heartbeat(execution_id, "worker-1", first.lease_id) is False
    assert broker.heartbeat(execution_id, "worker-1", second.lease_id) is True
    assert broker.mark_done(execution_id, "worker-1", first.lease_id, {"status": "failed"}) is None
    assert (
        broker.mark_done(execution_id, "worker-1", second.lease_id, {"status": "completed"})
        == second.lease_id
    )


def test_sweep_does_not_requeue_a_just_completed_node(job_db) -> None:
    """finish committed (lease released) but mark_done pending: sweep must skip."""
    execution_id = _seed_request(job_db, job_id="finish-race-job")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register(registry, "worker-1")
    broker = AgentExecutionBroker(TEST_DATABASE_URL, lease_ttl_seconds=1)
    claim = broker.claim("worker-1")
    assert claim is not None
    with job_db.connect() as conn:
        # Simulate completion.finish() having landed: lease released, node completed.
        conn.execute("update executor_leases set status='released' where id=?", (claim.lease_id,))
        conn.execute(
            "update job_nodes set status='completed', finished_at=current_timestamp"
            " where job_id='finish-race-job' and node_key='generate'"
        )
        conn.execute(
            "update agent_execution_requests set heartbeat_at=? where execution_id=?",
            (datetime.now(UTC) - timedelta(seconds=10), execution_id),
        )

    assert broker.sweep_expired_claims() == []

    node = job_db.get_job_node("finish-race-job", "generate")
    assert node["status"] == "completed"


def test_claim_rotates_across_workspaces(job_db) -> None:
    """A deep queue in one workspace must not starve another workspace."""
    for index in range(6):
        _seed_request(job_db, job_id=f"big-job-{index}", workspace_id="big-workspace")
    for index in range(2):
        _seed_request(job_db, job_id=f"small-job-{index}", workspace_id="small-workspace")
    # Force a global-FIFO-hostile ordering: every big-workspace request is older.
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set queued_at=? where workspace_id='big-workspace'",
            (datetime.now(UTC) - timedelta(hours=1),),
        )
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register(registry, "worker-1")
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    claims = [broker.claim("worker-1") for _ in range(4)]

    workspaces = {claim.workspace_id for claim in claims if claim is not None}
    assert workspaces == {"big-workspace", "small-workspace"}


def test_claim_skips_paused_job_and_cancels_failed_job(job_db) -> None:
    paused_execution = _seed_request(job_db, job_id="paused-job")
    failed_execution = _seed_request(job_db, job_id="failed-job")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register(registry, "worker-1")
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    with job_db.connect() as conn:
        conn.execute("update jobs set execution_paused=1 where id='paused-job'")
        conn.execute("update jobs set status='failed' where id='failed-job'")

    assert broker.claim("worker-1") is None

    with job_db._connect_read() as conn:
        rows = {
            row["execution_id"]: row["state"]
            for row in conn.execute(
                "select execution_id, state from agent_execution_requests"
            ).fetchall()
        }
    # Paused job stays queued for resume; failed job's request is cancelled.
    assert rows[paused_execution] == "queued"
    assert rows[failed_execution] == "cancelled"
    assert job_db.get_job("failed-job")["status"] == "failed"

    # After resume, the paused job's request becomes claimable.
    with job_db.connect() as conn:
        conn.execute("update jobs set execution_paused=0 where id='paused-job'")
    assert broker.claim("worker-1") is not None


def test_cross_node_same_workspace_race_exactly_one_winner(job_db) -> None:
    """Two DIFFERENT nodes of the SAME workspace share one workspace cap:
    with cap=1, a cross-node oversell race yields exactly one winner."""
    _seed_request(job_db, job_id="xn-job-a", node_key="generate-a", workspace_cap=1)
    _seed_request(job_db, job_id="xn-job-b", node_key="generate-b", workspace_cap=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    for worker_id in ("worker-1", "worker-2"):
        _register(registry, worker_id)

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def race(worker_id: str) -> None:
        barrier.wait(timeout=10)
        results[worker_id] = AgentExecutionBroker(TEST_DATABASE_URL).claim(worker_id)

    threads = [threading.Thread(target=race, args=(w,)) for w in ("worker-1", "worker-2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    winners = [claim for claim in results.values() if claim is not None]
    assert len(winners) == 1
    assert _claimed_count(job_db) == 1


def test_different_workspaces_do_not_block_each_other(job_db) -> None:
    """A saturated workspace must not block claims in another workspace."""
    _seed_request(job_db, job_id="ws-a-job-1", workspace_id="ws-a", workspace_cap=1)
    _seed_request(job_db, job_id="ws-a-job-2", workspace_id="ws-a", workspace_cap=1)
    _seed_request(job_db, job_id="ws-b-job-1", workspace_id="ws-b", workspace_cap=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register(registry, "worker-1", max_concurrency=4)
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    first = broker.claim("worker-1")
    second = broker.claim("worker-1")
    third = broker.claim("worker-1")

    claimed_workspaces = {
        claim.workspace_id for claim in (first, second, third) if claim is not None
    }
    # ws-a saturates after one claim; ws-b still gets its slot.
    assert claimed_workspaces == {"ws-a", "ws-b"}
    assert _claimed_count(job_db) == 2


def test_workspace_without_capacity_row_is_unlimited(job_db) -> None:
    """Documented fallback: no workspace_agent_capacities row = no limit."""
    for index in range(12):
        _seed_request(job_db, job_id=f"uncapped-job-{index}", workspace_cap=None)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    _register(registry, "worker-1", max_concurrency=12)
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    claims = [broker.claim("worker-1") for _ in range(12)]

    assert all(claim is not None for claim in claims)
    assert _claimed_count(job_db) == 12


def test_stale_definition_requests_are_failed_by_sweeper(job_db) -> None:
    execution_id = _seed_request(job_db, job_id="stale-def-job")
    # Republish the Agent with changed content: the pinned hash is now gone.
    sync_agent_definitions(
        TEST_DATABASE_URL, {"generator-v1": _definition(skill="question/generate-v2")}
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    assert broker.fail_stale_definition_requests() == [execution_id]

    node = job_db.get_job_node("stale-def-job", "generate")
    assert node["status"] == "failed"
    assert "disabled or changed" in node["error_message"]
    assert node["failure_category"] == "technical"
    assert node["failure_detail"] == "stale_definition"
    assert job_db.get_job("stale-def-job")["status"] == "failed"
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=?",
            (execution_id,),
        ).fetchone()
    assert row["state"] == "done"
    # The synthetic failed run makes the sweep visible to failed-node-runs
    # views and rerun-by-failure.
    runs = job_db.list_failed_node_runs("test-workspace", category="technical")
    assert [
        (str(run["job_id"]), str(run["node_key"]), str(run["failure_detail"])) for run in runs
    ] == [("stale-def-job", "generate", "stale_definition")]
