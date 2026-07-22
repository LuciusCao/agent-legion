from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_catalog import AgentDefinition, sync_agent_definitions
from server.app.agent_workers import AgentWorkerRegistry
from tests.postgres_support import TEST_DATABASE_URL


def _seed_request(job_db, *, job_id: str, node_key: str = "generate", limit: int = 20) -> None:
    definition = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    sync_agent_definitions(TEST_DATABASE_URL, {"generator-v1": definition})
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values ('test-workspace', 'Test')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (?, 'test-workspace', 'questions', 'question', ?)",
            (job_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (?, ?)", (job_id, node_key))
        conn.execute(
            "insert into workspace_node_routes(workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values ('test-workspace', 'questions', ?, 'agent', 'generator-v1')"
            " on conflict(workspace_id, workflow_key, node_key) do nothing",
            (node_key,),
        )
        conn.execute(
            "insert into workspace_node_capacities(workspace_id, workflow_key, node_key, max_concurrency)"
            " values ('test-workspace', 'questions', ?, ?)"
            " on conflict(workspace_id, workflow_key, node_key) do update"
            " set max_concurrency=excluded.max_concurrency",
            (node_key, limit),
        )
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    assert broker.enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id=job_id,
            workflow_key="questions",
            node_key=node_key,
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            node_concurrency_limit=limit,
            manifest={"job_id": job_id, "log_path": f"logs/{job_id}.log"},
        )
    )


def test_worker_registration_declares_runtime_and_machine_capacity(job_db) -> None:
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    token = registry.issue_token(
        worker_id="home-mini",
        name="Home Mac mini",
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )

    worker = registry.authenticate(token)

    assert worker is not None
    assert worker["runtimes"] == ["pi"]
    assert worker["max_concurrency"] == 10
    assert "capability" not in worker


def test_claim_starts_node_and_consumes_both_capacity_domains(job_db) -> None:
    _seed_request(job_db, job_id="job-1", limit=1)
    _seed_request(job_db, job_id="job-2", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    first = broker.claim("worker-1")
    second = broker.claim("worker-1")

    assert first is not None
    assert second is None
    assert job_db.get_job_node("job-1", "generate")["status"] == "running"
    assert job_db.get_job_node("job-2", "generate")["status"] == "pending"


def test_worker_machine_capacity_is_shared_across_nodes(job_db) -> None:
    _seed_request(job_db, job_id="job-1", node_key="generate-a", limit=20)
    _seed_request(job_db, job_id="job-2", node_key="generate-b", limit=20)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=1,
        labels={"arch": "arm64"},
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    assert broker.claim("worker-1") is not None
    assert broker.claim("worker-1") is None


def test_claim_skips_saturated_node_and_uses_capacity_on_another_node(job_db) -> None:
    _seed_request(job_db, job_id="job-a1", node_key="generate-a", limit=1)
    _seed_request(job_db, job_id="job-a2", node_key="generate-a", limit=1)
    _seed_request(job_db, job_id="job-b1", node_key="generate-b", limit=1)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=2,
        labels={"arch": "arm64"},
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    first = broker.claim("worker-1")
    second = broker.claim("worker-1")

    assert first is not None and first.node_key == "generate-a"
    assert second is not None and second.node_key == "generate-b"
    assert job_db.get_job_node("job-a2", "generate-a")["status"] == "pending"


def test_incompatible_worker_does_not_claim_or_start_node(job_db) -> None:
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["openclaw"],
        max_concurrency=10,
        labels={"arch": "amd64"},
    )

    assert AgentExecutionBroker(TEST_DATABASE_URL).claim("worker-1") is None
    assert job_db.get_job_node("job-1", "generate")["status"] == "pending"


def test_expired_worker_claim_is_requeued_for_another_worker(job_db) -> None:
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    for worker_id in ("worker-1", "worker-2"):
        registry.issue_token(
            worker_id=worker_id,
            name=worker_id,
            runtimes=["pi"],
            max_concurrency=1,
            labels={"arch": "arm64"},
        )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, lease_ttl_seconds=1)
    first = broker.claim("worker-1")
    assert first is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=? where execution_id=?",
            (datetime.now(UTC) - timedelta(seconds=10), first.execution_id),
        )

    assert broker.sweep_expired_claims() == [first.execution_id]
    second = broker.claim("worker-2")

    assert second is not None
    assert second.execution_id == first.execution_id
    assert second.lease_id != first.lease_id


def test_node_twenty_and_three_workers_ten_never_claim_more_than_twenty(job_db) -> None:
    for index in range(30):
        _seed_request(job_db, job_id=f"job-{index}", limit=20)
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    for worker_id in ("worker-1", "worker-2", "worker-3"):
        registry.issue_token(
            worker_id=worker_id,
            name=worker_id,
            runtimes=["pi"],
            max_concurrency=10,
            labels={"arch": "arm64"},
        )
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    claimed = [
        broker.claim(worker_id)
        for worker_id in ("worker-1", "worker-2", "worker-3")
        for _ in range(10)
    ]

    assert sum(claim is not None for claim in claimed) == 20
    with job_db._connect_read() as conn:
        rows = conn.execute(
            "select worker_id, count(*) as cnt from agent_execution_requests"
            " where state='claimed' group by worker_id order by worker_id"
        ).fetchall()
    assert sum(int(row["cnt"]) for row in rows) == 20
    assert all(int(row["cnt"]) <= 10 for row in rows)


def test_sweep_closes_request_when_lease_already_finished(job_db) -> None:
    """A crash between finish() and mark_done() must not strand a claimed
    request or requeue a completed node: the sweep closes it instead."""
    _seed_request(job_db, job_id="job-1")
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=1,
        labels={"arch": "arm64"},
    )
    broker = AgentExecutionBroker(TEST_DATABASE_URL, lease_ttl_seconds=1)
    claim = broker.claim("worker-1")
    assert claim is not None
    with job_db.connect() as conn:
        conn.execute(
            "update agent_execution_requests set heartbeat_at=? where execution_id=?",
            (datetime.now(UTC) - timedelta(seconds=10), claim.execution_id),
        )
        # Simulate the result path having finished the lease already.
        conn.execute(
            "update executor_leases set status='released' where id=?",
            (claim.lease_id,),
        )

    assert broker.sweep_expired_claims() == []

    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where execution_id=?",
            (claim.execution_id,),
        ).fetchone()
    assert row is not None
    assert row["state"] == "done"


def test_reap_terminal_bundles_removes_done_bundles_and_stale_archives(job_db, tmp_path) -> None:
    _seed_request(job_db, job_id="job-1")
    bundle_dir = tmp_path / "bundles"
    bundle_dir.mkdir()
    live_bundle = bundle_dir / "live.tar.gz"
    live_bundle.write_bytes(b"bundle")
    done_bundle = bundle_dir / "done.tar.gz"
    done_bundle.write_bytes(b"bundle")
    fresh_archive = bundle_dir / "fresh.result.tar.gz"
    fresh_archive.write_bytes(b"archive")
    stale_archive = bundle_dir / "stale.result.tar.gz"
    stale_archive.write_bytes(b"archive")
    old = datetime.now(UTC).timestamp() - 7200
    os.utime(stale_archive, (old, old))

    broker = AgentExecutionBroker(TEST_DATABASE_URL, bundle_dir=bundle_dir)
    with job_db.connect() as conn:
        # The seeded request stays queued (its bundle must survive); add a
        # terminal request pointing at done.tar.gz.
        conn.execute(
            "update agent_execution_requests set manifest_json=? where job_id='job-1'",
            (json.dumps({"bundle_name": "live.tar.gz"}),),
        )
        conn.execute(
            "insert into agent_execution_requests("
            " execution_id, workspace_id, job_id, workflow_key, node_key,"
            " agent_id, agent_definition_hash, node_concurrency_limit,"
            " state, queued_at, manifest_json)"
            " values ('exec-done', 'test-workspace', 'job-1', 'questions', 'review',"
            " 'generator-v1', 'sha256:whatever', 1, 'done', current_timestamp, ?)",
            (json.dumps({"bundle_name": "done.tar.gz"}),),
        )

    reaped = broker.reap_terminal_bundles()

    assert reaped == 2
    assert live_bundle.is_file()
    assert not done_bundle.exists()
    assert fresh_archive.is_file()
    assert not stale_archive.exists()
