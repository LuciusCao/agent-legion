"""Retry-failed endpoint HTTP contract (#1358): 202 accept + handle, guard
matrix (404 unknown/cross-workspace, 409 non-terminal, 400 meaningless kind /
empty failed set), auth (401 anon; the router-level reject_studio_agent_scope
is pinned by the effecting-surface pins in test_workspace_api_token_boundaries).
Service/query semantics live in tests/services/test_job_batch_operations_retry.py.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from server.app.services.job_batch_operations import BatchOpsExecutors, JobBatchOpsFeeder
from server.app.services.job_packages import JobPackageService
from server.app.services.job_service_factory import JobServices
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from tests.helpers import publish_legacy_intake_revision

WORKSPACE = "batch-retry-route-ws"


def _create_workspace(client: TestClient) -> str:
    response = client.post("/api/workspaces", json={"id": WORKSPACE, "name": "batch-retry"})
    assert response.status_code == 200, response.text
    publish_legacy_intake_revision(client.app.state.job_db, WORKSPACE)
    return WORKSPACE


def _insert_operation(
    client: TestClient,
    kind: str,
    results: list[tuple[str, str]],
    *,
    params: dict[str, Any] | None = None,
    status: str = "completed",
) -> str:
    """One op row whose results table carries (job_id, status) pairs."""
    job_db = client.app.state.job_db
    op_id = f"op-{uuid.uuid4().hex[:12]}"
    job_db.create_job_batch_operation(
        op_id,
        WORKSPACE,
        kind,
        {"job_ids": [job_id for job_id, _ in results], "params": params or {}},
        len(results),
        "tester",
    )
    job_db.insert_job_batch_operation_results(
        op_id,
        [
            {
                "seq": seq,
                "job_id": job_id,
                "status": result_status,
                "reason_code": "not_found" if result_status == "failed" else None,
                "message": None,
                "payload": {},
            }
            for seq, (job_id, result_status) in enumerate(results)
        ],
    )
    if status != "pending":
        job_db.transition_job_batch_operation(
            op_id, from_statuses=("pending", "running"), to_status=status
        )
    return op_id


def _drain(client: TestClient) -> None:
    app = client.app
    job_db = app.state.job_db
    settings = app.state.settings
    services = JobServices(
        job_db,
        settings,
        WorkspaceExecutionConfigurationService(job_db, settings),
        None,
        None,
    )
    feeder = JobBatchOpsFeeder(
        job_db,
        settings,
        BatchOpsExecutors(
            rerun=services.rerun,
            deletion=services.deletion,
            execution=services.execution,
            pause=services.pause,
            upgrade=services.workflow_upgrade,
            packages=JobPackageService(job_db, settings),
            approvals=services.approvals,
        ),
    )
    while feeder.consume_once():
        pass


def test_retry_failed_returns_202_handle_and_executes(client: TestClient) -> None:
    ws = _create_workspace(client)
    job_db = client.app.state.job_db
    created = client.post(
        f"/api/workspaces/{ws}/job-batches",
        json={
            "workflow_key": ws,
            "source_kind": "direct_ids",
            "knowledge_point_ids": ["RT-1"],
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["jobs"][0]["id"]
    job_db.update_job_status(job_id, "failed")
    op_id = _insert_operation(
        client,
        "rerun",
        [(job_id, "failed"), ("missing-job", "failed"), ("other-job", "succeeded")],
        params={"node_key": "write_script", "from_failed_node": False},
    )

    accepted = client.post(f"/api/workspaces/{ws}/job-batch-operations/{op_id}/retry-failed")

    assert accepted.status_code == 202, accepted.text
    operation = accepted.json()["operation"]
    assert operation["kind"] == "rerun"
    assert operation["status"] == "pending"
    # 只含失败项（succeeded/skipped 不进重试选择）。
    assert operation["items_total"] == 2
    assert operation["created_by"].startswith("user:")
    retry_id = operation["id"]
    assert retry_id != op_id

    _drain(client)

    status = client.get(f"/api/workspaces/{ws}/job-batch-operations/{retry_id}")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] == "completed"
    assert (body["succeeded_count"], body["failed_count"]) == (1, 1)
    items = client.get(f"/api/workspaces/{ws}/job-batch-operations/{retry_id}/results").json()[
        "results"
    ]
    by_job = {item["result"]["job_id"]: item["result"] for item in items}
    assert by_job[job_id]["status"] == "succeeded"
    assert by_job[job_id]["node_key"] == "write_script"
    assert by_job["missing-job"]["reason_code"] == "not_found"
    # 原 op 不被改写。
    original = client.get(f"/api/workspaces/{ws}/job-batch-operations/{op_id}").json()
    assert original["status"] == "completed"
    original_results = client.get(
        f"/api/workspaces/{ws}/job-batch-operations/{op_id}/results"
    ).json()["results"]
    assert len(original_results) == 3


def test_retry_failed_guard_matrix(client: TestClient) -> None:
    ws = _create_workspace(client)
    base = f"/api/workspaces/{ws}/job-batch-operations"
    running = _insert_operation(client, "rerun", [("j-1", "failed")], status="running")
    pause = _insert_operation(client, "pause", [("j-1", "failed")])
    clean = _insert_operation(client, "delete", [("j-1", "succeeded")])

    # 未知 id / 非终态 / 无意义 kind / 无失败项。
    assert client.post(f"{base}/op-missing/retry-failed").status_code == 404
    assert client.post(f"{base}/{running}/retry-failed").status_code == 409
    assert client.post(f"{base}/{pause}/retry-failed").status_code == 400
    assert client.post(f"{base}/{clean}/retry-failed").status_code == 400
    # 跨 workspace 引用同形 404（不泄漏存在性）。
    other = "batch-retry-route-other"
    assert client.post("/api/workspaces", json={"id": other, "name": other}).status_code == 200
    retryable = _insert_operation(client, "delete", [("j-9", "failed")])
    assert (
        client.post(
            f"/api/workspaces/{other}/job-batch-operations/{retryable}/retry-failed"
        ).status_code
        == 404
    )


def test_retry_failed_requires_auth(anon_client: TestClient) -> None:
    assert (
        anon_client.post(
            f"/api/workspaces/{WORKSPACE}/job-batch-operations/op-1/retry-failed"
        ).status_code
        == 401
    )
