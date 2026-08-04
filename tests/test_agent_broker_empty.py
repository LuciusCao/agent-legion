"""Empty-claim restock trigger: debounce, true-empty probe, and callback wiring."""

from __future__ import annotations

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_catalog import AgentDefinition, sync_agent_definitions
from server.app.agent_workers import AgentWorkerRegistry
from tests.postgres_support import TEST_DATABASE_URL


def _seed_queued_request(job_db, *, job_id: str, workspace_id: str = "test-workspace") -> None:
    definition = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    sync_agent_definitions(TEST_DATABASE_URL, {"generator-v1": definition})
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (?, 'Test') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (?, ?, 'questions', 'question', ?)",
            (job_id, workspace_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (?, 'generate')", (job_id,))
        conn.execute(
            "insert into workspace_node_routes(workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values (?, 'questions', 'generate', 'agent', 'generator-v1')"
            " on conflict(workspace_id, workflow_key, node_key) do nothing",
            (workspace_id,),
        )
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    assert broker.enqueue(
        AgentExecutionRequest(
            workspace_id=workspace_id,
            job_id=job_id,
            workflow_key="questions",
            node_key="generate",
            agent_id="generator-v1",
            agent_definition_hash=definition.definition_hash(),
            manifest={
                "job_id": job_id,
                "log_path": f"logs/{job_id}.log",
                "pi": {"provider": "gateway", "model": "test-model"},
            },
        )
    )


def _register_worker(worker_id: str, *, labels: dict[str, str] | None = None) -> None:
    registry = AgentWorkerRegistry(TEST_DATABASE_URL)
    registry.issue_token(
        worker_id=worker_id,
        name="worker",
        runtimes=["pi"],
        max_concurrency=10,
        labels=labels or {"arch": "arm64"},
    )


def test_empty_claim_on_empty_queue_fires_restock_once_per_debounce(job_db) -> None:
    _register_worker("worker-empty-1")
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    calls: list[int] = []
    broker.empty_claim.on_empty_queue = lambda: calls.append(1)

    assert broker.claim("worker-empty-1") is None
    assert broker.claim("worker-empty-1") is None

    assert len(calls) == 1


def test_restock_fires_again_after_debounce(job_db) -> None:
    _register_worker("worker-empty-2")
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    calls: list[int] = []
    broker.empty_claim.on_empty_queue = lambda: calls.append(1)

    assert broker.claim("worker-empty-2") is None
    broker.empty_claim._last_fired -= broker.empty_claim.debounce_seconds
    assert broker.claim("worker-empty-2") is None

    assert len(calls) == 2


def test_empty_claim_with_queued_stock_does_not_fire(job_db) -> None:
    _seed_queued_request(job_db, job_id="job-stocked")
    # Worker cannot run the only queued request (label mismatch), so the claim
    # comes back empty while stock exists: no restock signal.
    _register_worker("worker-mismatch", labels={"arch": "x86"})
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    calls: list[int] = []
    broker.empty_claim.on_empty_queue = lambda: calls.append(1)

    assert broker.claim("worker-mismatch") is None

    assert calls == []


def test_successful_claim_does_not_fire(job_db) -> None:
    _seed_queued_request(job_db, job_id="job-claimable")
    _register_worker("worker-hungry")
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    calls: list[int] = []
    broker.empty_claim.on_empty_queue = lambda: calls.append(1)

    assert broker.claim("worker-hungry") is not None

    assert calls == []


def test_empty_claim_without_callback_is_noop(job_db) -> None:
    _register_worker("worker-noop")
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    assert broker.claim("worker-noop") is None
