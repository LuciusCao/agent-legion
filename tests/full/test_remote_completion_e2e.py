"""Full-gate evidence for EXEC-REMOTE-001."""

from __future__ import annotations

import dataclasses
import io
import tarfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.db.schema import init_db
from server.app.executors.leases import ExecutorLeaseRepository
from server.app.executors.remote_broker import RemoteExecutionBroker, RemoteExecutionPayload
from server.app.jobs import JobQueries
from server.app.remote_wiring import register_remote_completion
from server.app.routes.remote import create_remote_router
from tests.executors.leases.helpers import _claim_request, _setup_workspace

pytestmark = pytest.mark.full_gate


def _empty_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz"):
        pass
    return buffer.getvalue()


def test_remote_completion_drill(tmp_path: Path, settings) -> None:
    admin_token = "full-gate-admin-token"
    remote = settings.executor_runtime.remote.model_copy(update={"worker_token": admin_token})
    runtime = settings.executor_runtime.model_copy(update={"remote": remote})
    configured = dataclasses.replace(settings, executor_runtime=runtime)
    db_path = tmp_path / "jobs.sqlite"
    init_db(db_path)
    job_db = JobQueries(db_path, tmp_path / "jobs")
    workspace_id, job_id = _setup_workspace(
        job_db,
        "ws",
        "pi-remote",
        1,
        node_key="review_keywords",
        local_limit=None,
    )
    leases = ExecutorLeaseRepository(db_path, job_db=job_db)
    broker = RemoteExecutionBroker(db_path, tmp_path / "bundles")
    try:
        register_remote_completion(broker, leases, tmp_path / "jobs", None)
        app = FastAPI()
        app.include_router(create_remote_router(broker, configured), prefix="/api")
        client = TestClient(app)
        claim = leases.try_claim(
            _claim_request(
                workspace_id,
                job_id,
                executor_id="pi-remote",
                local_node_limit=None,
            )
        )
        assert claim is not None
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
        issued = client.post(
            "/api/remote/workers/register",
            json={
                "worker_id": "w1",
                "name": "w1",
                "capabilities": ["review_keywords"],
                "slots": 1,
            },
            headers={"X-Worker-Token": admin_token},
        )
        token = str(issued.json()["worker_token"])
        headers = {"X-Worker-Token": token, "X-Worker-Id": "w1"}
        claimed = client.post(
            "/api/remote/claim",
            json={
                "worker_id": "w1",
                "capabilities": ["review_keywords"],
                "worker_version": 1,
            },
            headers=headers,
        )
        assert claimed.status_code == 200
        reported = client.post(
            f"/api/remote/executions/{claim.execution_id}/result",
            content=_empty_archive(),
            headers={
                **headers,
                "X-Remote-Result": '{"status": "completed", "exit_code": 0}',
            },
        )
        assert reported.status_code == 204
        broker.wait_idle()
        assert leases.lease_status(claim.lease_id) == "released"
        assert job_db.get_job_node(job_id, claim.node_key)["status"] == "completed"
        assert job_db.get_job(job_id)["status"] == "completed"
    finally:
        broker.close()
