from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.remote_broker import RemoteExecutionBroker, RemoteExecutionPayload
from server.app.jobs import JobQueries
from tests.executors.leases.helpers import (
    _claim_request,
    _create_job_in_workspace,
    _setup_workspace,
)
from tests.postgres_support import TEST_DATABASE_URL

pytestmark = pytest.mark.full_gate


def _payload(index: int) -> RemoteExecutionPayload:
    return RemoteExecutionPayload(
        execution_id=f"execution-{index}",
        lease_id=f"lease-{index}",
        job_id=f"job-{index}",
        node_key="generate",
        capability="content.generate",
        bundle_name=f"bundle-{index}.zip",
        manifest={},
    )


def test_300_agents_claim_distinct_work_without_duplicates(tmp_path: Path) -> None:
    broker = RemoteExecutionBroker(TEST_DATABASE_URL, tmp_path / "bundles")
    for index in range(300):
        broker.register_worker(f"worker-{index}", f"Worker {index}", ["content.generate"], slots=1)
        broker.submit(_payload(index))

    with ThreadPoolExecutor(max_workers=64) as pool:
        claims = list(
            pool.map(
                lambda index: broker.dequeue(f"worker-{index}", ["content.generate"]),
                range(300),
            )
        )

    execution_ids = [claim.execution_id for claim in claims if claim is not None]
    assert len(execution_ids) == 300
    assert len(set(execution_ids)) == 300


def test_concurrent_requests_cannot_oversubscribe_worker_slots(tmp_path: Path) -> None:
    broker = RemoteExecutionBroker(TEST_DATABASE_URL, tmp_path / "bundles")
    broker.register_worker("shared", "Shared", ["content.generate"], slots=5)
    for index in range(100):
        broker.submit(_payload(index))

    with ThreadPoolExecutor(max_workers=64) as pool:
        claims = list(
            pool.map(lambda _: broker.dequeue("shared", ["content.generate"]), range(100))
        )

    execution_ids = [claim.execution_id for claim in claims if claim is not None]
    assert len(execution_ids) == 5
    assert len(set(execution_ids)) == 5


def test_concurrent_schedulers_cannot_exceed_executor_capacity(tmp_path: Path) -> None:
    queries = JobQueries(TEST_DATABASE_URL, tmp_path / "jobs")
    workspace_id, first_job = _setup_workspace(
        queries,
        "Concurrent capacity",
        "shared-executor",
        workspace_limit=50,
        local_limit=None,
    )
    job_ids = [first_job] + [_create_job_in_workspace(queries, workspace_id) for _ in range(49)]
    repository = ExecutorLeaseRepository(TEST_DATABASE_URL, data_dir=tmp_path)

    def claim(job_id: str):
        return repository.try_claim(
            _claim_request(
                workspace_id,
                job_id,
                executor_id="shared-executor",
                global_capacity=7,
                local_node_limit=None,
            )
        )

    with ThreadPoolExecutor(max_workers=50) as pool:
        claims = list(pool.map(claim, job_ids))

    assert sum(claim is not None for claim in claims) == 7
