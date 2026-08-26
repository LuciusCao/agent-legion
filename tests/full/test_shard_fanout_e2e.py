"""Full-gate evidence for EXEC-SHARD-001."""

from __future__ import annotations

from pathlib import Path

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


def test_shard_fanout_aggregates_to_failed(tmp_path: Path) -> None:
    executor = FakeShardExecutor(fail_shards={2})
    worker, job_db, job, _job_dir = _make_e2e(tmp_path, _over_definition(), executor)
    try:
        assert _poll_until(
            worker,
            lambda: (
                len(rows := _node_shards(worker.leases.path, job["id"], "review")) == 4
                and all(row["status"] in ("completed", "failed") for row in rows)
            ),
        )
        assert _node_status(job_db, job["id"], "review") == "failed"
        statuses = sorted(
            row["status"] for row in _node_shards(worker.leases.path, job["id"], "review")
        )
        assert statuses == ["completed"] * 3 + ["failed"]
        assert job_db.get_job(job["id"])["status"] == "failed"
        assert _node_status(job_db, job["id"], "aggregate") == "pending"
    finally:
        worker.stop()
