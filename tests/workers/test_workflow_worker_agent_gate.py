"""Per-pass agent gate preparation: batched loads and refresh semantics."""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

from server.app.workflow_worker.agent_gate import (
    AgentPassState,
    agent_claim_allowed,
    prepare_agent_pass,
    request_restock,
)
from server.app.workflow_worker.agent_stock import AgentStockConfig, StockBucket, StockSnapshot
from server.app.workflow_worker.ready_cache import ReadyCandidate
from server.app.workflow_worker.routing import NodeRoute
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode

_BATCH = "server.app.workflow_worker.agent_gate.batch.active_request_keys"
_STOCK = "server.app.workflow_worker.agent_gate.load_stock_snapshot"
_CATALOG = "server.app.workflow_worker.agent_gate.has_published_agent_definitions"


def _node(key: str = "fetch") -> WorkflowNode:
    return WorkflowNode(key=key, label=key, capability="fetch", outputs=["output.json"])


def _candidate(definition: WorkflowDefinition, node: WorkflowNode, job_id: str) -> ReadyCandidate:
    return ReadyCandidate(
        definition=definition,
        job={"id": job_id},
        node=node,
        job_dir=Path("."),
        control_snapshot={},
        allowed=frozenset(),
    )


def _worker(*, stock: AgentStockConfig | None = None) -> MagicMock:
    worker = MagicMock()
    worker.settings.executor_runtime.agent_stock = stock or AgentStockConfig()
    worker._route_cache = {}
    worker._agent_pass = AgentPassState()
    return worker


def test_prepare_skips_queries_without_agent_definitions() -> None:
    worker = _worker()
    node = _node()
    definition = WorkflowDefinition(
        key="test", label="Test", intake=WorkflowIntake(), nodes={node.key: node}
    )
    queues = {"ws1": deque([_candidate(definition, node, "job1")])}

    with patch(_CATALOG, return_value=False), patch(_BATCH) as batch, patch(_STOCK) as stock:
        prepare_agent_pass(worker, queues)

    batch.assert_not_called()
    stock.assert_not_called()


def test_prepare_batch_loads_and_filters_by_route_cache() -> None:
    worker = _worker()
    node = _node()
    definition = WorkflowDefinition(
        key="test", label="Test", intake=WorkflowIntake(), nodes={node.key: node}
    )
    # Cached non-agent route: excluded from the batch query without any DB.
    worker._route_cache[("ws1", "test", node.key)] = (
        time.monotonic(),
        NodeRoute("executor", target_id="local-default"),
    )
    other = WorkflowNode(key="review", label="review", capability="review", outputs=["o.json"])
    definition2 = WorkflowDefinition(
        key="test2", label="Test2", intake=WorkflowIntake(), nodes={other.key: other}
    )
    queues = {
        "ws1": deque([_candidate(definition, node, "job-exec")]),
        "ws2": deque([_candidate(definition2, other, "job-agent")]),
    }
    snapshot = StockSnapshot(config=AgentStockConfig())

    with (
        patch(_CATALOG, return_value=True),
        patch(_BATCH, return_value={("job-agent", "review")}) as batch,
        patch(_STOCK, return_value=snapshot),
    ):
        prepare_agent_pass(worker, queues)

    batch.assert_called_once_with(worker.agent_dispatch.broker.database_dsn, ["job-agent"])
    assert worker._agent_pass.active_nodes == {("job-agent", "review")}
    assert worker._agent_pass.stock_snapshot is snapshot


def test_prepare_skips_stock_when_disabled() -> None:
    worker = _worker(stock=AgentStockConfig(enabled=False))
    node = _node()
    definition = WorkflowDefinition(
        key="test", label="Test", intake=WorkflowIntake(), nodes={node.key: node}
    )
    queues = {"ws1": deque([_candidate(definition, node, "job1")])}

    with (
        patch(_CATALOG, return_value=True),
        patch(_BATCH, return_value=set()),
        patch(_STOCK) as stock,
    ):
        prepare_agent_pass(worker, queues)

    stock.assert_not_called()
    assert worker._agent_pass.stock_snapshot is None


def test_stock_snapshot_refreshes_on_interval() -> None:
    worker = _worker(stock=AgentStockConfig(refresh_seconds=30.0))
    node = _node()
    definition = WorkflowDefinition(
        key="test", label="Test", intake=WorkflowIntake(), nodes={node.key: node}
    )
    queues = {"ws1": deque([_candidate(definition, node, "job1")])}

    with (
        patch(_CATALOG, return_value=True),
        patch(_BATCH, return_value=set()),
        patch(_STOCK) as stock,
    ):
        stock.return_value = StockSnapshot(config=AgentStockConfig())
        prepare_agent_pass(worker, queues)
        prepare_agent_pass(worker, queues)
        assert stock.call_count == 1
        worker._agent_pass.stock_loaded_at -= 31.0
        prepare_agent_pass(worker, queues)
        assert stock.call_count == 2


def test_agent_claim_allowed_counts_enqueued_since_snapshot() -> None:
    worker = _worker()
    state = worker._agent_pass
    state.stock_snapshot = StockSnapshot(
        config=AgentStockConfig(min_stock=2, max_stock=10),
        buckets={("ws1", "agent-x"): StockBucket(queued=1)},
    )
    # Snapshot says 1 of target 2 stocked: one more submission fits.
    assert agent_claim_allowed(worker, "ws1", "job1", "fetch", "agent-x") is True
    # The refresh window keeps the snapshot frozen at queued=1, but the
    # submission counter must close the hole once the target is reached.
    state.stock_enqueued[("ws1", "agent-x")] = 1
    assert agent_claim_allowed(worker, "ws1", "job2", "fetch", "agent-x") is False
    assert state.stock_gated == 1
    # Other pairs are unaffected.
    assert agent_claim_allowed(worker, "ws2", "job2", "fetch", "agent-x") is True


def test_stock_reload_clears_enqueued_counter() -> None:
    worker = _worker(stock=AgentStockConfig(refresh_seconds=30.0))
    node = _node()
    definition = WorkflowDefinition(
        key="test", label="Test", intake=WorkflowIntake(), nodes={node.key: node}
    )
    queues = {"ws1": deque([_candidate(definition, node, "job1")])}

    with (
        patch(_CATALOG, return_value=True),
        patch(_BATCH, return_value=set()),
        patch(_STOCK) as stock,
    ):
        stock.return_value = StockSnapshot(config=AgentStockConfig())
        prepare_agent_pass(worker, queues)
        worker._agent_pass.stock_enqueued[("ws1", "agent-x")] = 5
        # Within the window the counter survives (snapshot stays frozen).
        prepare_agent_pass(worker, queues)
        assert worker._agent_pass.stock_enqueued == {("ws1", "agent-x"): 5}
        # On reload the fresh snapshot sees the real queued rows again.
        worker._agent_pass.stock_loaded_at -= 31.0
        prepare_agent_pass(worker, queues)
        assert worker._agent_pass.stock_enqueued == {}


def test_agent_claim_allowed_skips_in_flight_submission() -> None:
    worker = _worker()
    state = worker._agent_pass
    # Submitted to the enqueue pool but not yet visible in the DB: the
    # pass must not resubmit a duplicate bundle build for it.
    state.in_flight = {("job1", "fetch")}
    assert agent_claim_allowed(worker, "ws1", "job1", "fetch", "agent-x") is False
    # Other nodes/jobs of the same pair stay claimable.
    assert agent_claim_allowed(worker, "ws1", "job1", "review", "agent-x") is True
    assert agent_claim_allowed(worker, "ws1", "job2", "fetch", "agent-x") is True


def test_reset_pass_clears_per_pass_fields_but_keeps_snapshot() -> None:
    state = AgentPassState(
        active_nodes={("j", "n")},
        pool_full=True,
        stock_gated=3,
        stock_snapshot=StockSnapshot(config=AgentStockConfig()),
        stock_loaded_at=123.0,
        stock_enqueued={("ws1", "agent-x"): 2},
        in_flight={("j2", "n2")},
    )
    state.reset_pass()
    assert state.active_nodes == set()
    assert state.pool_full is False
    assert state.stock_gated == 0
    assert state.stock_snapshot is not None
    assert state.stock_loaded_at == 123.0
    # Tied to the snapshot's lifetime, not the pass's.
    assert state.stock_enqueued == {("ws1", "agent-x"): 2}
    # Tied to the pool closure, cleared only when the submission finishes.
    assert state.in_flight == {("j2", "n2")}


def test_agent_claim_allowed_gates() -> None:
    worker = _worker()
    state = worker._agent_pass
    state.active_nodes = {("job1", "fetch")}
    assert agent_claim_allowed(worker, "ws1", "job1", "fetch", "agent-x") is False

    state.active_nodes = set()
    state.stock_snapshot = StockSnapshot(
        config=AgentStockConfig(min_stock=2, max_stock=10),
        buckets={("ws1", "agent-x"): StockBucket(queued=2)},
    )
    assert agent_claim_allowed(worker, "ws1", "job1", "fetch", "agent-x") is False
    assert state.stock_gated == 1
    assert agent_claim_allowed(worker, "ws2", "job1", "fetch", "agent-x") is True

    state.stock_snapshot = None
    assert agent_claim_allowed(worker, "ws1", "job1", "fetch", "agent-x") is True


def test_force_refresh_expires_stock_snapshot() -> None:
    state = AgentPassState(
        stock_snapshot=StockSnapshot(config=AgentStockConfig()),
        stock_loaded_at=123.0,
    )
    state.force_refresh()
    assert state.stock_loaded_at == 0.0
    assert state.stock_snapshot is not None  # kept; reloaded by the next pass


def test_request_restock_expires_snapshot_and_wakes_worker() -> None:
    worker = _worker()
    worker._agent_pass.stock_loaded_at = 123.0
    request_restock(worker)
    assert worker._agent_pass.stock_loaded_at == 0.0
    worker._wake_event.set.assert_called_once_with()
