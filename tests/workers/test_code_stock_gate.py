"""Code stockpile gate (issue #125): bound queued kind='code' stock.

The gate throttles code-Worker enqueue production: global queued code stock
must stay below the online code fleet's declared capacity x factor (floored
by min_stock, capped at max_stock); over-target nodes stay pending for the
local pool. Without it an online code Worker let every ready code node
enqueue, flooding the broker queue (2026-08-18 prod incident).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from server.app.agent_broker import AgentExecutionBroker
from server.app.agent_workers import AgentWorkerRegistry
from server.app.workflow_worker.code_claim import try_claim_code_worker_node
from server.app.workflow_worker.code_stock import CodeStockConfig, CodeStockGate
from tests.postgres_support import TEST_DATABASE_URL
from tests.services.test_code_claim import _enqueue_code, _insert_code_job_rows
from tests.workers.helpers import _local_node


def _gate(config: CodeStockConfig | None = None) -> CodeStockGate:
    # A fresh gate per assertion: the TTL cache is exercised in production,
    # the tests pin the target math against the live tables.
    return CodeStockGate(TEST_DATABASE_URL, config or CodeStockConfig())


def _register_code_fleet(*, max_code_concurrency: int = 2) -> None:
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-code-stock",
        name="worker",
        runtimes=["pi"],
        capabilities=["package"],
        max_concurrency=1,
        max_code_concurrency=max_code_concurrency,
        protocol_version=2,
    )


def _enqueue_code_stock(job_db, *, start: int, count: int) -> None:
    broker = AgentExecutionBroker(TEST_DATABASE_URL, data_dir=job_db.jobs_dir.parent)
    for index in range(start, start + count):
        _insert_code_job_rows(job_db, job_id=f"job-stock-{index}")
        _enqueue_code(broker, job_id=f"job-stock-{index}")


def test_gate_blocks_at_target_and_recovers(job_db) -> None:
    """target = max(ceil(2 x 1.5), min_stock=8): the 8th queued row closes
    the gate, draining below target reopens it."""
    _register_code_fleet()
    _enqueue_code_stock(job_db, start=0, count=7)
    assert _gate().allows() is True
    _enqueue_code_stock(job_db, start=7, count=8)  # 15 queued, above target

    assert _gate().allows() is False

    with job_db.connect() as conn:
        conn.execute("delete from agent_execution_requests where state='queued' and kind='code'")
    assert _gate().allows() is True


def test_target_scales_with_fleet_and_caps_at_max_stock(job_db) -> None:
    """capacity 2 x factor 100 = 200, capped at max_stock=5."""
    _register_code_fleet()
    config = CodeStockConfig(factor=100.0, min_stock=0, max_stock=5)
    _enqueue_code_stock(job_db, start=0, count=4)
    assert _gate(config).allows() is True
    _enqueue_code_stock(job_db, start=4, count=5)  # 9 queued, above the cap

    assert _gate(config).allows() is False


def test_empty_fleet_falls_back_to_min_stock(job_db) -> None:
    """No online code Worker: capacity 0, so min_stock alone bounds stock."""
    config = CodeStockConfig(min_stock=2, max_stock=5)
    assert _gate(config).allows() is True
    _enqueue_code_stock(job_db, start=0, count=2)

    assert _gate(config).allows() is False


@pytest.mark.no_db
def test_disabled_gate_always_allows() -> None:
    assert CodeStockGate(TEST_DATABASE_URL, CodeStockConfig(enabled=False)).allows() is True


@pytest.mark.no_db
def test_stock_gate_blocks_code_worker_enqueue() -> None:
    """Gate closed: try_claim_code_worker_node leaves the node to the local
    pool (False) without marking it in flight for a Worker."""
    dispatch = MagicMock()
    dispatch.is_in_flight.return_value = False
    dispatch.broker.has_active_request.return_value = False
    dispatch.online_code_worker_available.return_value = True
    worker = MagicMock()
    worker.code_dispatch = dispatch
    worker.code_stock.allows.return_value = False

    handled = try_claim_code_worker_node(
        worker,
        {"id": "ws-1"},
        {"id": "job-1"},
        _local_node("fetch"),
        Path("/tmp/job"),
        Path("/tmp/claim.log"),
        (),
        "test",
    )

    assert handled is False
    dispatch.try_mark_in_flight.assert_not_called()
