"""Full-gate evidence for EXEC-SHARD-001."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.workflows.test_sharding import (
    FakeShardExecutor,
    _make_e2e,
    _node_shards,
    _node_status,
    _over_definition,
    _poll_until,
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


def test_shard_fanout_aggregates_to_failed(tmp_path: Path) -> None:
    executor = FakeShardExecutor(fail_shards={2})
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, _over_definition(), executor)
    try:
        assert _poll_until(
            worker,
            lambda: _node_status(job_db, job["id"], "review") == "failed",
        )
        statuses = sorted(
            row["status"] for row in _node_shards(worker.leases.path, job["id"], "review")
        )
        assert statuses == ["completed"] * 3 + ["failed"]
        assert job_db.get_job(job["id"])["status"] == "failed"
        assert _node_status(job_db, job["id"], "aggregate") == "pending"
    finally:
        worker.stop()
