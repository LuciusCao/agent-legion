from __future__ import annotations

from pathlib import Path

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.remote_broker import RemoteExecutionBroker, RemoteExecutionPayload
from server.app.jobs import JobQueries
from server.app.remote_wiring import register_remote_completion
from tests.executors.leases.helpers import _claim_request, _setup_workspace
from tests.postgres_support import TEST_DATABASE_URL


def test_completion_callback_finishes_lease_without_workflow_worker(tmp_path: Path) -> None:
    db_path = TEST_DATABASE_URL
    job_db = JobQueries(db_path, tmp_path / "jobs")
    workspace_id, job_id = _setup_workspace(job_db, "ws", "pi-remote", 1, local_limit=None)
    leases = ExecutorLeaseRepository(db_path, job_db=job_db, data_dir=tmp_path)
    claim = leases.try_claim(
        _claim_request(
            workspace_id,
            job_id,
            executor_id="pi-remote",
            local_node_limit=None,
        )
    )
    assert claim is not None
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    try:
        register_remote_completion(broker, leases, tmp_path / "jobs", None)
        broker.submit(
            RemoteExecutionPayload(
                execution_id=claim.execution_id,
                lease_id=claim.lease_id,
                job_id=job_id,
                node_key=claim.node_key,
                capability="review_keywords",
                bundle_name=f"{claim.execution_id}.tar.gz",
                manifest={
                    "job_id": job_id,
                    "node_key": claim.node_key,
                    "run_token": "tok",
                    "expected_outputs": [],
                },
            )
        )
        broker.cancel(claim.execution_id)
        broker.wait_idle()
        assert leases.lease_status(claim.lease_id) == "released"
    finally:
        broker.close()
