from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.db.transaction import read_connection, write_transaction
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.jobs import JobQueries
from server.app.workflows.sharding import materialize_shards
from tests.executors.leases.helpers import _claim_request, _setup_workspace
from tests.postgres_support import TEST_DATABASE_URL


def test_expired_shard_lease_fails_shard_and_aggregates(tmp_path: Path) -> None:
    job_db = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    repo = ExecutorLeaseRepository(job_db.path, job_db=job_db, data_dir=tmp_path)
    workspace_id, job_id = _setup_workspace(
        job_db,
        "ws",
        "pi-default",
        2,
        local_limit=None,
    )
    with write_transaction(job_db.path) as conn:
        materialize_shards(
            conn,
            job_id,
            "review_keywords",
            [{"q": 0}, {"q": 1}],
            max_shards=4,
        )
    claim = repo.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            executor_id="pi-default",
            local_node_limit=None,
            shard_index=0,
        )
    )
    assert claim is not None
    past = datetime.now(UTC) - timedelta(seconds=10)
    with write_transaction(job_db.path) as conn:
        conn.execute(
            "update executor_leases set expires_at=? where id=?",
            (past.strftime("%Y-%m-%d %H:%M:%S.%f"), claim.lease_id),
        )

    assert repo.expire_stale(datetime.now(UTC)) == [claim.lease_id]
    with read_connection(job_db.path) as conn:
        shards = conn.execute(
            "select * from node_shards where job_id=? and node_key=? order by shard_index",
            (job_id, "review_keywords"),
        ).fetchall()
    assert shards[0]["status"] == "failed"
    assert shards[0]["error_message"] == "lease expired"
    assert shards[1]["status"] == "pending"
    node = job_db.get_job_node(job_id, "review_keywords")
    assert node is not None and node["status"] == "failed"
    assert node["error_message"] == "lease expired"
