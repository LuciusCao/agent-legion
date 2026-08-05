"""Cheap gates and per-pass memoization on the workflow worker claim path."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from server.app.executors.scheduling.capacity import CapacitySnapshot
from server.app.workflow_worker.agent_claim import cached_batch_payload
from server.app.workflow_worker.agent_gate import AgentPassState
from server.app.workflow_worker.agent_stock import AgentStockConfig, StockBucket, StockSnapshot
from server.app.workflow_worker.maintenance import WorkflowMaintenance
from server.app.workflow_worker.routing import NodeRoute
from server.app.workflow_worker.schedule import try_claim_and_submit
from server.app.workflows.definition import WorkflowDefinition, WorkflowIntake, WorkflowNode


def _node(key: str = "fetch") -> WorkflowNode:
    return WorkflowNode(key=key, label=key, capability="fetch", outputs=["output.json"])


def _definition(node: WorkflowNode) -> WorkflowDefinition:
    return WorkflowDefinition(
        key="test", label="Test", intake=WorkflowIntake(), nodes={node.key: node}
    )


def _worker(tmp_path: Path, route: NodeRoute, node: WorkflowNode) -> MagicMock:
    worker = MagicMock()
    worker.settings.logs_dir = tmp_path
    worker.settings.executor_definitions = {}
    worker._batch_payload_cache = {}
    worker._pass_claim_counts = {}
    worker._agent_pass = AgentPassState()
    worker._route_cache = {("ws1", "test", node.key): (time.monotonic(), route)}
    return worker


def _snapshot(global_remaining: int, workspace_remaining: int = 0) -> CapacitySnapshot:
    return CapacitySnapshot(
        global_remaining={"local-default": global_remaining},
        workspace_remaining={("local-default", "ws1"): workspace_remaining},
    )


def test_executor_capacity_gate_skips_batch_lookup(tmp_path: Path) -> None:
    node = _node()
    worker = _worker(tmp_path, NodeRoute("executor", target_id="local-default"), node)
    worker.registry.global_capacity.return_value = 2
    claimed = try_claim_and_submit(
        worker,
        {"id": "ws1"},
        _definition(node),
        {"id": "job1", "batch_id": "b1"},
        node,
        tmp_path,
        None,
        None,
        _snapshot(global_remaining=0),
    )
    assert claimed is False
    worker.job_db.get_batch.assert_not_called()


def test_batch_payload_memoized_within_pass(tmp_path: Path) -> None:
    worker = MagicMock()
    worker._batch_payload_cache = {}
    worker.job_db.get_batch.return_value = {
        "source_payload_json": '{"node_config": {"fetch": {"bank_version": "frozen"}}}'
    }
    job_a: dict[str, Any] = {"id": "job-a", "batch_id": "b1"}
    job_b: dict[str, Any] = {"id": "job-b", "batch_id": "b1"}

    first = cached_batch_payload(worker, job_a)
    second = cached_batch_payload(worker, job_b)

    assert first == second == {"node_config": {"fetch": {"bank_version": "frozen"}}}
    worker.job_db.get_batch.assert_called_once_with("b1")


def test_batch_payload_none_without_batch_id(tmp_path: Path) -> None:
    worker = MagicMock()
    worker._batch_payload_cache = {}
    assert cached_batch_payload(worker, {"id": "job-a", "batch_id": ""}) is None
    worker.job_db.get_batch.assert_not_called()


def test_agent_active_request_gate_skips_config_and_enqueue(tmp_path: Path) -> None:
    node = _node()
    worker = _worker(tmp_path, NodeRoute("agent", target_id="agent-x"), node)
    worker.settings.agent_definitions = {"agent-x": MagicMock(config_schema={})}
    # The per-pass batched filter says this (job, node) already has a request.
    worker._agent_pass.active_nodes = {("job1", "fetch")}

    claimed = try_claim_and_submit(
        worker,
        {"id": "ws1"},
        _definition(node),
        {"id": "job1", "batch_id": "b1"},
        node,
        tmp_path,
        None,
        None,
        CapacitySnapshot(),
    )

    assert claimed is False
    worker.agent_dispatch.enqueue.assert_not_called()
    worker.job_db.get_batch.assert_not_called()


def test_agent_pool_full_flag_skips_rest_of_pass(tmp_path: Path) -> None:
    node = _node()
    worker = _worker(tmp_path, NodeRoute("agent", target_id="agent-x"), node)
    worker.settings.agent_definitions = {"agent-x": MagicMock(config_schema={})}
    worker._agent_pass.pool_full = True

    claimed = try_claim_and_submit(
        worker,
        {"id": "ws1"},
        _definition(node),
        {"id": "job1", "batch_id": "b1"},
        node,
        tmp_path,
        None,
        None,
        CapacitySnapshot(),
    )

    assert claimed is False
    # Cheap skip: no pool submission, no batch lookup, no enqueue.
    worker.agent_dispatch.enqueue_pool.submit.assert_not_called()
    worker.job_db.get_batch.assert_not_called()
    worker.agent_dispatch.enqueue.assert_not_called()


def test_agent_stock_gate_skips_when_stocked_to_target(tmp_path: Path) -> None:
    node = _node()
    worker = _worker(tmp_path, NodeRoute("agent", target_id="agent-x"), node)
    worker.settings.agent_definitions = {"agent-x": MagicMock(config_schema={})}
    worker._agent_pass.stock_snapshot = StockSnapshot(
        config=AgentStockConfig(min_stock=2, max_stock=10),
        buckets={("ws1", "agent-x"): StockBucket(queued=2)},
    )

    claimed = try_claim_and_submit(
        worker,
        {"id": "ws1"},
        _definition(node),
        {"id": "job1", "batch_id": "b1"},
        node,
        tmp_path,
        None,
        None,
        CapacitySnapshot(),
    )

    assert claimed is False
    assert worker._agent_pass.stock_gated == 1
    worker.agent_dispatch.enqueue_pool.submit.assert_not_called()
    worker.job_db.get_batch.assert_not_called()


def test_agent_enqueue_counts_pass_claim(tmp_path: Path) -> None:
    node = _node()
    worker = _worker(tmp_path, NodeRoute("agent", target_id="agent-x"), node)
    worker.settings.agent_definitions = {"agent-x": MagicMock(config_schema={})}
    worker.agent_dispatch.enqueue_pool.submit.return_value = True
    worker.job_db.get_batch.return_value = {"source_payload_json": "{}"}

    claimed = try_claim_and_submit(
        worker,
        {"id": "ws1"},
        _definition(node),
        {"id": "job1", "batch_id": "b1"},
        node,
        tmp_path,
        None,
        None,
        CapacitySnapshot(),
    )

    assert claimed is True
    assert worker._pass_claim_counts == {"agent:agent-x": 1}
    # The submission counts toward the stock gate within the snapshot window.
    assert worker._agent_pass.stock_enqueued == {("ws1", "agent-x"): 1}
    # The enqueue itself is submitted as a background closure.
    closure = worker.agent_dispatch.enqueue_pool.submit.call_args.args[0]
    worker.agent_dispatch.enqueue.return_value = True
    closure()
    worker.agent_dispatch.enqueue.assert_called_once()


def test_agent_stock_gate_uses_enqueued_counter_within_window(tmp_path: Path) -> None:
    node = _node()
    worker = _worker(tmp_path, NodeRoute("agent", target_id="agent-x"), node)
    worker.settings.agent_definitions = {"agent-x": MagicMock(config_schema={})}
    worker.agent_dispatch.enqueue_pool.submit.return_value = True
    worker.job_db.get_batch.return_value = {"source_payload_json": "{}"}
    # Frozen snapshot: nothing stocked yet, target 1 (min_stock floor).
    worker._agent_pass.stock_snapshot = StockSnapshot(
        config=AgentStockConfig(min_stock=1, max_stock=10),
        buckets={},
    )

    first = try_claim_and_submit(
        worker,
        {"id": "ws1"},
        _definition(node),
        {"id": "job1", "batch_id": "b1"},
        node,
        tmp_path,
        None,
        None,
        CapacitySnapshot(),
    )
    second = try_claim_and_submit(
        worker,
        {"id": "ws1"},
        _definition(node),
        {"id": "job2", "batch_id": "b1"},
        node,
        tmp_path,
        None,
        None,
        CapacitySnapshot(),
    )

    assert first is True
    # Same frozen snapshot, but the first submission fills the target.
    assert second is False
    assert worker._agent_pass.stock_gated == 1
    assert worker.agent_dispatch.enqueue_pool.submit.call_count == 1


def test_agent_enqueue_skipped_when_pool_full(tmp_path: Path) -> None:
    node = _node()
    worker = _worker(tmp_path, NodeRoute("agent", target_id="agent-x"), node)
    worker.settings.agent_definitions = {"agent-x": MagicMock(config_schema={})}
    worker.agent_dispatch.enqueue_pool.submit.return_value = False
    worker.job_db.get_batch.return_value = {"source_payload_json": "{}"}

    claimed = try_claim_and_submit(
        worker,
        {"id": "ws1"},
        _definition(node),
        {"id": "job1", "batch_id": "b1"},
        node,
        tmp_path,
        None,
        None,
        CapacitySnapshot(),
    )

    assert claimed is False
    # A rejected submission raises the per-pass flag for the skip gate.
    assert worker._agent_pass.pool_full is True
    assert worker._pass_claim_counts == {}
    worker.agent_dispatch.enqueue.assert_not_called()


def test_agent_enqueue_config_error_fails_node(tmp_path: Path) -> None:
    node = _node()
    worker = _worker(tmp_path, NodeRoute("agent", target_id="agent-x"), node)
    worker.settings.agent_definitions = {"agent-x": MagicMock(config_schema={})}
    worker.agent_dispatch.enqueue_pool.submit.return_value = True
    worker.agent_dispatch.enqueue.side_effect = ValueError("bad route")
    worker.job_db.get_batch.return_value = {"source_payload_json": "{}"}

    assert (
        try_claim_and_submit(
            worker,
            {"id": "ws1"},
            _definition(node),
            {"id": "job1", "batch_id": "b1"},
            node,
            tmp_path,
            None,
            None,
            CapacitySnapshot(),
        )
        is True
    )
    closure = worker.agent_dispatch.enqueue_pool.submit.call_args.args[0]
    closure()
    worker.leases.fail_without_lease.assert_called_once()


def test_executor_claim_counts_pass_claim(tmp_path: Path) -> None:
    node = _node()
    worker = _worker(tmp_path, NodeRoute("executor", target_id="local-default"), node)
    worker.registry.global_capacity.return_value = 2
    worker.job_db.get_batch.return_value = {"source_payload_json": "{}"}

    with patch("server.app.workflow_worker.schedule.claim_executor_node", return_value=True):
        claimed = try_claim_and_submit(
            worker,
            {"id": "ws1"},
            _definition(node),
            {"id": "job1", "batch_id": "b1"},
            node,
            tmp_path,
            None,
            None,
            _snapshot(global_remaining=2, workspace_remaining=2),
        )

    assert claimed is True
    assert worker._pass_claim_counts == {"local-default": 1}


def test_maintenance_cleanup_runs_off_caller_thread(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def _cleanup(*args: Any, **kwargs: Any) -> tuple[int, int]:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=10)
        return (0, 0)

    settings = MagicMock()
    # interval 0: on fresh-boot Linux CI runners time.monotonic() (≈ uptime)
    # is smaller than the default 3600s interval, so maybe_cleanup would skip
    # the thread entirely and started.wait() would time out deterministically.
    settings.config = {"cleanup": {"interval_seconds": 0}}
    settings.data_dir = tmp_path
    maintenance = WorkflowMaintenance(MagicMock(), settings)

    with patch("server.app.workflow_worker.maintenance.cleanup_old_logs", side_effect=_cleanup):
        maintenance.maybe_cleanup()
        # Generous wait: thread startup/scheduling can be delayed on loaded
        # parallel-gate runners.
        assert started.wait(timeout=30)
        # A second invocation while the first is still running must not
        # launch another cleanup thread.
        maintenance.maybe_cleanup()
        release.set()
        deadline = time.monotonic() + 30
        while maintenance._cleanup_running and time.monotonic() < deadline:
            time.sleep(0.01)

    assert calls == 1
    assert maintenance._cleanup_running is False
