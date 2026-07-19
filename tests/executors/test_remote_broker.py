from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from server.app.db.schema import init_db
from server.app.executors.remote_broker import (
    RemoteExecutionBroker,
    RemoteExecutionPayload,
    RemoteOutcome,
)


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def broker(tmp_path: Path) -> RemoteExecutionBroker:
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    return RemoteExecutionBroker(db_path, tmp_path / "bundles", claim_timeout_seconds=60.0)


def _payload(execution_id: str = "e1", capability: str = "cap_a") -> RemoteExecutionPayload:
    return RemoteExecutionPayload(
        execution_id=execution_id,
        lease_id=f"lease-{execution_id}",
        job_id="job1",
        node_key="node_a",
        capability=capability,
        bundle_name=f"{execution_id}.tar.gz",
        manifest={"job_id": "job1", "node_key": "node_a"},
    )


def test_dequeue_empty_returns_none(broker):
    assert broker.dequeue("w1", {"cap_a"}) is None


def test_submit_then_dequeue_fifo_and_capability_filter(broker):
    broker.submit(_payload("e1", "cap_a"))
    broker.submit(_payload("e2", "cap_b"))
    assert broker.dequeue("w1", {"cap_b"}).execution_id == "e2"
    assert broker.dequeue("w1", {"cap_a", "cap_b"}).execution_id == "e1"
    assert broker.dequeue("w1", {"cap_a"}) is None


def test_claim_bundle_url(broker):
    broker.submit(_payload("e1"))
    claim = broker.dequeue("w1", {"cap_a"})
    assert claim.bundle_url == "/api/remote/executions/e1/bundle"


def test_claim_carries_command_spec(broker):
    spec = {"version": 1, "prompt": "work in {job_dir}", "command": ["pi", "@{prompt_file}"]}
    broker.submit(replace(_payload("e1"), command_spec=spec))
    claim = broker.dequeue("w1", {"cap_a"})
    assert claim is not None and claim.command_spec == spec

    # Legacy submissions without a spec stay None (backward-compatible default).
    broker.submit(_payload("e2"))
    claim = broker.dequeue("w1", {"cap_a"})
    assert claim is not None and claim.command_spec is None

    # The spec is persisted with the row: a restarted broker still serves it.
    broker.submit(replace(_payload("e3"), command_spec=spec))
    restarted = RemoteExecutionBroker(
        broker._db_path, broker.bundle_dir, claim_timeout_seconds=60.0
    )
    claim = restarted.dequeue("w2", {"cap_a"})
    assert claim is not None and claim.execution_id == "e3"
    assert claim.command_spec == spec


def test_heartbeat_only_for_claiming_worker(broker):
    broker.submit(_payload("e1"))
    broker.dequeue("w1", {"cap_a"})
    assert broker.heartbeat("e1", "w1") is True
    assert broker.heartbeat("e1", "w2") is False


def test_stale_claim_is_requeued(broker):
    broker.submit(_payload("e1"))
    broker.dequeue("w1", {"cap_a"})
    broker._entries["e1"].last_heartbeat_at = _now() - timedelta(seconds=120)
    claim = broker.dequeue("w2", {"cap_a"})  # lazy sweep inside dequeue
    assert claim is not None and claim.execution_id == "e1"
    assert broker._entries["e1"].requeue_count == 1


def test_requeue_limit_fails_execution(broker):
    broker_limited = RemoteExecutionBroker(
        broker._db_path, broker.bundle_dir, claim_timeout_seconds=60.0, requeue_limit=1
    )
    broker_limited.submit(_payload("e1"))
    broker_limited.dequeue("w1", {"cap_a"})
    broker_limited._entries["e1"].last_heartbeat_at = _now() - timedelta(seconds=120)
    broker_limited.dequeue("w2", {"cap_a"})  # requeue #1
    broker_limited._entries["e1"].last_heartbeat_at = _now() - timedelta(seconds=120)
    assert broker_limited.dequeue("w3", {"cap_a"}) is None  # limit exceeded -> failed
    outcome = broker_limited.wait_result("e1")
    assert outcome.status == "failed"
    assert "requeue limit" in outcome.error_message


def test_complete_unblocks_wait_result(broker):
    broker.submit(_payload("e1"))
    broker.dequeue("w1", {"cap_a"})
    outcome = RemoteOutcome(status="completed", exit_code=0, result_archive_name="e1.result.tar.gz")
    thread = threading.Thread(target=broker.complete, args=("e1", "w1", outcome))
    thread.start()
    assert broker.wait_result("e1") == replace(outcome, worker_id="w1")
    thread.join(timeout=5)


def test_complete_is_deduplicated(broker):
    broker.submit(_payload("e1"))
    broker.dequeue("w1", {"cap_a"})
    outcome = RemoteOutcome(status="completed", exit_code=0)
    assert broker.complete("e1", "w1", outcome) is True
    assert broker.complete("e1", "w1", outcome) is False


def test_cancel_before_claim_completes_as_cancelled(broker):
    broker.submit(_payload("e1"))
    broker.cancel("e1")
    assert broker.dequeue("w1", {"cap_a"}) is None
    assert broker.wait_result("e1").status == "cancelled"


def test_cancel_after_claim_makes_heartbeat_fail(broker):
    broker.submit(_payload("e1"))
    broker.dequeue("w1", {"cap_a"})
    broker.cancel("e1")
    assert broker.heartbeat("e1", "w1") is False
    assert broker.wait_result("e1").status == "cancelled"


def test_bundle_name_for_only_claiming_worker(broker):
    broker.submit(_payload("e1"))
    assert broker.bundle_name_for("e1", "w1") is None  # not claimed yet
    broker.dequeue("w1", {"cap_a"})
    assert broker.bundle_name_for("e1", "w1") == "e1.tar.gz"
    assert broker.bundle_name_for("e1", "w2") is None


def test_finish_injects_claiming_worker_id(broker):
    broker.submit(_payload("e1"))
    broker.dequeue("w1", {"cap_a"})
    forged = RemoteOutcome(status="completed", exit_code=0, worker_id="w-evil")
    assert broker.complete("e1", "w1", forged) is True
    outcome = broker.wait_result("e1")
    assert outcome.worker_id == "w1"  # broker claim record wins over reported metadata


def test_worker_registry_round_trip(broker):
    broker.register_worker("w1", "mac-mini", ["cap_a"], 65)
    broker.register_worker("w1", "mac-mini", ["cap_a", "cap_b"], 70)  # upsert
    workers = broker.list_workers()
    assert workers == [
        {
            "worker_id": "w1",
            "name": "mac-mini",
            "capabilities": ["cap_a", "cap_b"],
            "slots": 70,
            "registered_at": workers[0]["registered_at"],
            "last_seen_at": workers[0]["last_seen_at"],
        }
    ]
    before = workers[0]["last_seen_at"]
    time.sleep(0.01)
    broker.touch_worker("w1")
    assert broker.list_workers()[0]["last_seen_at"] >= before
