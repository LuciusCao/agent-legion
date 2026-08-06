"""Claim-window paging, enqueue model guard, and blocked-queue diagnostics.

Regression tests for the 2026-08-01 queue deadlock (issue #13):
- scan rounds page past a saturated, fully unclaimable queue head;
- enqueue rejects placeholder/empty pi models instead of queueing poison;
- an empty claim with unclaimable stock logs a debounced blocked-queue
  WARNING (skip-reason histogram) instead of looking like a dry queue.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from server.app.agent_broker import AgentExecutionBroker, AgentExecutionRequest
from server.app.agent_catalog import AgentDefinition
from server.app.agent_workers import AgentWorkerRegistry
from tests.helpers import replace_agent_catalog
from tests.postgres_support import TEST_DATABASE_URL

_GOOD_MODEL = "test-model"
_POISON_MODEL = "poison-model"


def _insert_job_rows(
    job_db,
    *,
    job_id: str,
    node_key: str = "generate",
    workspace_id: str = "test-workspace",
    agent_id: str = "generator-v1",
) -> None:
    with job_db.connect() as conn:
        conn.execute(
            "insert into workspaces(id, name) values (%s, 'Test') on conflict(id) do nothing",
            (workspace_id,),
        )
        conn.execute(
            "insert into jobs(id, workspace_id, workflow_key, source_type, source_id)"
            " values (%s, %s, 'questions', 'question', %s)",
            (job_id, workspace_id, job_id),
        )
        conn.execute("insert into job_nodes(job_id, node_key) values (%s, %s)", (job_id, node_key))
        conn.execute(
            "insert into workspace_node_routes(workspace_id, workflow_key, node_key, target_kind, target_id)"
            " values (%s, 'questions', %s, 'agent', %s)"
            " on conflict(workspace_id, workflow_key, node_key) do nothing",
            (workspace_id, node_key, agent_id),
        )
        conn.execute(
            "insert into workspace_agent_capacities(workspace_id, max_concurrency)"
            " values (%s, 20) on conflict(workspace_id) do nothing",
            (workspace_id,),
        )


def _seed_definition(agent_id: str = "generator-v1") -> AgentDefinition:
    definition = AgentDefinition(
        capability="generate",
        runtime="pi",
        skill="question/generate",
        requires_labels={"arch": "arm64"},
    )
    replace_agent_catalog({agent_id: definition})
    return definition


def _enqueue(
    broker: AgentExecutionBroker,
    definition: AgentDefinition,
    *,
    job_id: str,
    model: str,
    workspace_id: str = "test-workspace",
) -> str | None:
    return broker.enqueue(
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
                "execution": {"provider": "gateway", "model": model},
            },
        )
    )


def _register_worker(models: list[dict[str, str]] | None = None) -> None:
    declarations: dict[str, Any] = {}
    if models is not None:
        declarations = {"capabilities": ["generate"], "models": models}
    # models=None keeps the registry's internal wildcard compatibility mode.
    AgentWorkerRegistry(TEST_DATABASE_URL).issue_token(
        worker_id="worker-1",
        name="worker",
        runtimes=["pi"],
        max_concurrency=10,
        labels={"arch": "arm64"},
        **declarations,
    )


def _pin_queue_order(job_db, job_ids: list[str]) -> None:
    base = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    with job_db.connect() as conn:
        for offset, job_id in enumerate(job_ids):
            conn.execute(
                "update agent_execution_requests set queued_at=%s where job_id=%s",
                (base + timedelta(seconds=offset), job_id),
            )


def test_claim_pages_past_poisoned_queue_head(job_db) -> None:
    """8+ unclaimable head entries must not starve the claimable tail."""
    definition = _seed_definition()
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    poison_jobs = [f"job-poison-{index}" for index in range(9)]
    for job_id in poison_jobs + ["job-good"]:
        _insert_job_rows(job_db, job_id=job_id)
    for job_id in poison_jobs:
        assert _enqueue(broker, definition, job_id=job_id, model=_POISON_MODEL) is not None
    assert _enqueue(broker, definition, job_id="job-good", model=_GOOD_MODEL) is not None
    _pin_queue_order(job_db, poison_jobs + ["job-good"])
    _register_worker(models=[{"provider": "gateway", "model": _GOOD_MODEL}])

    claimed = broker.claim("worker-1")

    assert claimed is not None
    assert claimed.job_id == "job-good"


def test_claim_with_fully_unclaimable_queue_returns_none(job_db) -> None:
    """A queue of only unclaimable requests ends the bounded scan with None."""
    definition = _seed_definition()
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    poison_jobs = [f"job-poison-{index}" for index in range(10)]
    for job_id in poison_jobs:
        _insert_job_rows(job_db, job_id=job_id)
        assert _enqueue(broker, definition, job_id=job_id, model=_POISON_MODEL) is not None
    _pin_queue_order(job_db, poison_jobs)
    _register_worker(models=[{"provider": "gateway", "model": _GOOD_MODEL}])

    assert broker.claim("worker-1") is None
    with job_db._connect_read() as conn:
        row = conn.execute(
            "select count(*) as c from agent_execution_requests where state='queued'"
        ).fetchone()
    assert row["c"] == 10


def test_enqueue_rejects_unresolved_execution_model(job_db) -> None:
    definition = _seed_definition()
    _insert_job_rows(job_db, job_id="job-1")
    broker = AgentExecutionBroker(TEST_DATABASE_URL)

    for bad_model in ("", "your-model"):
        with pytest.raises(ValueError, match="unresolved provider/model"):
            _enqueue(broker, definition, job_id="job-1", model=bad_model)
    with job_db._connect_read() as conn:
        row = conn.execute("select count(*) as c from agent_execution_requests").fetchone()
    assert row["c"] == 0


def test_empty_claim_with_blocked_queue_logs_skip_reasons(job_db, caplog) -> None:
    definition = _seed_definition()
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    poison_jobs = [f"job-poison-{index}" for index in range(3)]
    for job_id in poison_jobs:
        _insert_job_rows(job_db, job_id=job_id)
        assert _enqueue(broker, definition, job_id=job_id, model=_POISON_MODEL) is not None
    _register_worker(models=[{"provider": "gateway", "model": _GOOD_MODEL}])

    with caplog.at_level(logging.WARNING, logger="server.app.agent_broker.empty_diagnostics"):
        assert broker.claim("worker-1") is None

    warnings = [record.getMessage() for record in caplog.records]
    assert any("blocked queue" in message for message in warnings)
    assert any("capability_or_model_mismatch" in message for message in warnings)


def test_empty_claim_with_dry_queue_triggers_restock_without_warning(job_db, caplog) -> None:
    broker = AgentExecutionBroker(TEST_DATABASE_URL)
    restock = MagicMock()
    broker.empty_claim.on_empty_queue = restock
    _register_worker()

    with caplog.at_level(logging.WARNING, logger="server.app.agent_broker.empty_diagnostics"):
        assert broker.claim("worker-1") is None

    restock.assert_called_once_with()
    assert not caplog.records
