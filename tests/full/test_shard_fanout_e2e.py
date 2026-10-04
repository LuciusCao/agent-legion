"""Full-gate evidence for EXEC-SHARD-001."""

from __future__ import annotations

from pathlib import Path
from threading import Event

import pytest

from tests.helpers.sharding import (
    FakeShardExecutor,
)
from tests.helpers.sharding import (
    make_e2e as _make_e2e,
)
from tests.helpers.sharding import (
    node_shards as _node_shards,
)
from tests.helpers.sharding import (
    node_status as _node_status,
)
from tests.helpers.sharding import (
    over_definition as _over_definition,
)
from tests.helpers.sharding import (
    poll_until as _poll_until,
)

pytestmark = pytest.mark.full_gate


def test_shard_fanout_aggregates_to_completed(tmp_path: Path) -> None:
    executor = FakeShardExecutor()
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, _over_definition(), executor, capacity=2)
    try:
        assert _poll_until(
            worker,
            lambda: _node_status(job_db, job["id"], "aggregate") == "completed",
        )
        shards = _node_shards(worker.leases.path, job["id"], "review")
        assert [row["status"] for row in shards] == ["completed"] * 4
        assert job_db.get_job(job["id"])["status"] == "completed"
    finally:
        worker.stop()


@pytest.mark.parametrize("failed_shard", [0, 2, 3])
def test_shard_fanout_aggregates_to_failed(tmp_path: Path, failed_shard: int) -> None:
    # Any failure terminalizes the node, so later claims are intentionally
    # refused. Establish all four claims before allowing any shard to finish.
    gate = Event()
    executor = FakeShardExecutor(gate=gate, fail_shards={failed_shard})
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, _over_definition(), executor, capacity=4)
    try:
        assert _poll_until(
            worker,
            lambda: (
                [row["status"] for row in _node_shards(worker.leases.path, job["id"], "review")]
                == ["running"] * 4
            ),
        )
        gate.set()
        assert _poll_until(
            worker,
            lambda: (
                len(rows := _node_shards(worker.leases.path, job["id"], "review")) == 4
                and all(row["status"] in ("completed", "failed") for row in rows)
            ),
        ), _node_shards(worker.leases.path, job["id"], "review")
        assert _node_status(job_db, job["id"], "review") == "failed"
        statuses = sorted(
            row["status"] for row in _node_shards(worker.leases.path, job["id"], "review")
        )
        assert statuses == ["completed"] * 3 + ["failed"]
        assert job_db.get_job(job["id"])["status"] == "failed"
        assert _node_status(job_db, job["id"], "aggregate") == "pending"
        assert not any(context.node_key == "aggregate" for context in executor.contexts)
    finally:
        gate.set()
        worker.stop()


@pytest.mark.parametrize("failed_shard", [0, 2, 3])
def test_shard_failure_stops_unclaimed_siblings(tmp_path: Path, failed_shard: int) -> None:
    """Single capacity makes failure-before-the-next-claim deterministic."""
    executor = FakeShardExecutor(fail_shards={failed_shard})
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, _over_definition(), executor, capacity=1)
    try:
        assert _poll_until(worker, lambda: job_db.get_job(job["id"])["status"] == "failed")
        # Drive additional passes to verify terminal jobs cannot claim siblings
        # or the reduce node, rather than observing only the failure instant.
        for _ in range(3):
            worker._poll()
        shards = _node_shards(worker.leases.path, job["id"], "review")
        assert [row["status"] for row in shards] == (
            ["completed"] * failed_shard + ["failed"] + ["pending"] * (3 - failed_shard)
        )
        assert all(not row["execution_id"] for row in shards[failed_shard + 1 :])
        assert _node_status(job_db, job["id"], "review") == "failed"
        assert _node_status(job_db, job["id"], "aggregate") == "pending"
        assert not any(context.node_key == "aggregate" for context in executor.contexts)
    finally:
        worker.stop()
