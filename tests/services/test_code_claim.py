"""kind='code' claim path: self-contained payloads, dual capacity pools.

Batch 2 (design §7.1): code requests share the agent_execution_requests
table but skip the versioned_entities join and the provider/model matching;
Worker capacity is enforced as two independent pools (agent / code).
"""

from __future__ import annotations

import json

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_workers import AgentWorkerRegistry
from tests.postgres_support import TEST_DATABASE_URL
from tests.test_agent_broker import _seed_request


def _broker(data_dir) -> AgentExecutionBroker:
    return AgentExecutionBroker(TEST_DATABASE_URL, data_dir=data_dir)


def _insert_code_job_rows(job_db, *, job_id: str, node_key: str = "package") -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name, default_workflow_key) values ('test-workspace', 'Test', 'demo_workflow')"
            " on conflict(id) do nothing"
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, 'test-workspace', 'questions', 'question', %s)",
            (job_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))


def _enqueue_code(broker: AgentExecutionBroker, *, job_id: str, node_key: str = "package") -> str:
    execution_id = broker.enqueue(
        AgentExecutionRequest(
            workspace_id="test-workspace",
            job_id=job_id,
            workflow_key="questions",
            node_key=node_key,
            # No Agent definition exists for this pair: the claim must not
            # consult versioned_entities for kind='code' rows.
            agent_id="package",
            agent_definition_hash="codehash",
            manifest={
                "kind": "code",
                "capability": "package",
                "code_hash": "abc123",
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "config": {"mode": "fast"},
            },
            kind="code",
        )
    )
    assert execution_id is not None
    return execution_id


def _register_code_worker(
    worker_id: str = "worker-code",
    *,
    max_concurrency: int = 10,
    max_code_concurrency: int = 1,
    capabilities: list[str] | None = None,
) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id=worker_id,
        name="worker",
        runtimes=["pi"],
        capabilities=capabilities if capabilities is not None else ["package"],
        max_concurrency=max_concurrency,
        max_code_concurrency=max_code_concurrency,
        labels={"arch": "arm64"},
        protocol_version=2,
    )


def test_code_claim_skips_agent_definition_and_returns_kind(job_db) -> None:
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-1")
    execution_id = _enqueue_code(broker, job_id="job-1")
    _register_code_worker()

    claimed = broker.claim("worker-code")

    assert claimed is not None
    assert claimed.kind == "code"
    assert claimed.execution_id == execution_id
    assert claimed.manifest["code_hash"] == "abc123"
    assert claimed.manifest["config"] == {"mode": "fast"}
    with job_db._connect_read() as conn:
        lease = conn.execute(
            "select executor_id from executor_leases where execution_id=%s", (execution_id,)
        ).fetchone()
        run = conn.execute(
            "select status from node_runs where job_id='job-1' and node_key='package'"
        ).fetchone()
    assert lease["executor_id"] == "agent:code:package"
    assert run["status"] == "running"


def test_code_claim_records_config_snapshot(job_db) -> None:
    """The manifest config built at enqueue lands on node_runs as the
    dispatch-time audit snapshot (CONFIG-RUNTIME-MUTABLE-001)."""
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-1")
    _enqueue_code(broker, job_id="job-1")
    _register_code_worker()

    claimed = broker.claim("worker-code")

    assert claimed is not None
    with job_db._connect_read() as conn:
        run = conn.execute(
            "select config_snapshot_json from node_runs where job_id='job-1' and node_key='package'"
        ).fetchone()
    assert run is not None
    assert json.loads(run["config_snapshot_json"]) == {"mode": "fast"}


def test_code_claim_requires_declared_code_capacity(job_db) -> None:
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-1")
    _enqueue_code(broker, job_id="job-1")
    _register_code_worker(max_code_concurrency=0)

    assert broker.claim("worker-code") is None
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where job_id='job-1'"
        ).fetchone()
    assert row["state"] == "queued"


def test_code_claim_requires_matching_capability(job_db) -> None:
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-1")
    _enqueue_code(broker, job_id="job-1")
    _register_code_worker(capabilities=["other-capability"])

    assert broker.claim("worker-code") is None


def test_code_pool_full_skips_code_but_not_agent_candidates(job_db) -> None:
    """Dual pools: a saturated code pool never blocks agent claims."""
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-code-1")
    _insert_code_job_rows(job_db, job_id="job-code-2")
    _enqueue_code(broker, job_id="job-code-1")
    _enqueue_code(broker, job_id="job-code-2")
    _seed_request(job_db, job_id="job-agent", limit=20)
    _register_code_worker(
        max_concurrency=1, max_code_concurrency=1, capabilities=["package", "generate"]
    )

    first = broker.claim("worker-code")
    second = broker.claim("worker-code")
    third = broker.claim("worker-code")

    assert first is not None and second is not None
    # Both pools handed out exactly one claim; the second code request stays
    # queued behind the full code pool.
    assert {first.kind, second.kind} == {"agent", "code"}
    assert third is None
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select count(*) as c from agent_execution_requests where state='queued'"
        ).fetchone()
    assert row["c"] == 1


def test_claim_redeclared_code_capacity_takes_effect_without_reregister(job_db) -> None:
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-1")
    _enqueue_code(broker, job_id="job-1")
    _register_code_worker(max_code_concurrency=0)

    assert broker.claim("worker-code") is None
    claimed = broker.claim("worker-code", None, 2)

    assert claimed is not None
    assert claimed.kind == "code"
    worker = AgentWorkerRegistry(TEST_DATABASE_URL).list_workers()[0]
    assert worker["max_code_concurrency"] == 2


def test_fail_stale_definition_requests_ignores_code_rows(job_db) -> None:
    """kind='code' rows have no versioned definition by design; the staleness
    sweeper must not fail them as definition-less."""
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-1")
    _enqueue_code(broker, job_id="job-1")

    assert broker.fail_stale_definition_requests() == []
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where job_id='job-1'"
        ).fetchone()
    assert row["state"] == "queued"


def test_cancelled_code_executions_follows_job_control_state(job_db) -> None:
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-code")
    _enqueue_code(broker, job_id="job-code")
    _seed_request(job_db, job_id="job-agent", limit=20)
    _register_code_worker(
        max_concurrency=5, max_code_concurrency=1, capabilities=["package", "generate"]
    )
    code_claim = broker.claim("worker-code")
    agent_claim = broker.claim("worker-code")
    assert code_claim is not None and agent_claim is not None
    claims = {code_claim.kind: code_claim, agent_claim.kind: agent_claim}

    assert broker.cancelled_code_executions("worker-code") == []
    with job_db.connect() as conn:
        conn.execute("update jobs set execution_paused=1 where id='job-code'")

    cancelled = broker.cancelled_code_executions("worker-code")

    # Only the kind='code' execution is reported; the claimed agent execution
    # keeps the lease-expiry semantics (batch 2 decision 6).
    assert cancelled == [claims["code"].execution_id]


def test_register_rejects_code_capacity_below_protocol_v2(job_db) -> None:
    """v1 heartbeats carry no cancel body: code capacity requires v2."""
    with pytest.raises(ValueError, match="protocol_version"):
        AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
            worker_id="worker-v1-code",
            name="worker",
            runtimes=["pi"],
            capabilities=["package"],
            max_concurrency=10,
            max_code_concurrency=1,
            protocol_version=1,
        )


def test_code_claim_skips_worker_below_protocol_v2(job_db) -> None:
    """Defense in depth: a v1 row predating the register gate never claims code."""
    broker = _broker(job_db.jobs_dir.parent)
    _insert_code_job_rows(job_db, job_id="job-1")
    _enqueue_code(broker, job_id="job-1")
    with job_db.connect() as conn:
        conn.execute(
            "insert into agent_workers(worker_id, runtimes_json, max_concurrency,"
            " max_code_concurrency, capabilities_json, protocol_version, token_hash,"
            " registered_at, last_seen_at)"
            " values ('worker-v1', '[\"pi\"]', 10, 1, '[\"package\"]', 1, 'x',"
            " current_timestamp, current_timestamp)"
        )

    assert broker.claim("worker-v1") is None
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select state from agent_execution_requests where job_id='job-1'"
        ).fetchone()
    assert row["state"] == "queued"
