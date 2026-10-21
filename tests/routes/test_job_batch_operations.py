"""Batch ops async dispatch API (#946): 202 accept, status poll, results page.

The 8 batch endpoints keep the synchronous fast path at/below
``BATCH_OPS_ASYNC_THRESHOLD`` (byte-identical responses; the existing batch
route tests pin that) and return 202 + an operation handle above it. The
shared test app runs with background tasks dormant (tests/conftest.py), so
these tests drive ``JobBatchOpsFeeder.consume_once`` directly — deterministic
progression, no wall-clock waits.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.services.job_batch_operations import BatchOpsExecutors, JobBatchOpsFeeder
from server.app.services.job_packages import JobPackageService
from server.app.services.job_service_factory import JobServices
from server.app.services.workspace_execution_configuration import (
    WorkspaceExecutionConfigurationService,
)
from tests.helpers import publish_legacy_intake_revision

WORKSPACE = "education_video_problems_generation"


@pytest.fixture
def force_async(monkeypatch) -> None:
    """阈值归零：任何非空选择都走异步受理。"""
    monkeypatch.setattr(
        "server.app.services.job_batch_operations.service.BATCH_OPS_ASYNC_THRESHOLD", 0
    )


def _create_workspace(client: TestClient) -> str:
    response = client.post("/api/workspaces", json={"id": WORKSPACE, "name": "batch-ops"})
    assert response.status_code == 200, response.text
    publish_legacy_intake_revision(client.app.state.job_db, WORKSPACE)
    return WORKSPACE


def _create_job(client: TestClient, source_id: str) -> str:
    created = client.post(
        f"/api/workspaces/{WORKSPACE}/job-batches",
        json={
            "workflow_key": WORKSPACE,
            "source_kind": "direct_ids",
            "knowledge_point_ids": [source_id],
        },
    )
    assert created.status_code == 200, created.text
    return created.json()["jobs"][0]["id"]


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
        ),
    )
    while feeder.consume_once():
        pass


def test_large_pause_returns_202_handle_and_completes(
    client: TestClient, force_async: None
) -> None:
    ws = _create_workspace(client)
    job_ids = [_create_job(client, "QA1"), _create_job(client, "QA2")]

    accepted = client.post(
        f"/api/workspaces/{ws}/jobs/batch-pause",
        json={"job_ids": job_ids, "reason": "hold"},
    )

    assert accepted.status_code == 202, accepted.text
    operation = accepted.json()["operation"]
    assert operation["kind"] == "pause"
    assert operation["status"] == "pending"
    assert operation["items_total"] == 2
    assert operation["processed_count"] == 0
    assert operation["created_by"].startswith("user:")
    op_id = operation["id"]
    # 异步：受理时副作用尚未发生。
    for job_id in job_ids:
        detail = client.get(f"/api/jobs/{job_id}").json()["job"]
        assert detail["execution_control"]["paused"] is False

    _drain(client)

    status = client.get(f"/api/workspaces/{ws}/job-batch-operations/{op_id}")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["status"] == "completed"
    assert body["processed_count"] == 2
    assert (body["succeeded_count"], body["skipped_count"], body["failed_count"]) == (2, 0, 0)
    assert body["reason_counts"] == {}
    assert body["finished_at"] is not None
    for job_id in job_ids:
        detail = client.get(f"/api/jobs/{job_id}").json()["job"]
        assert detail["execution_control"]["paused"] is True

    results = client.get(f"/api/workspaces/{ws}/job-batch-operations/{op_id}/results")
    assert results.status_code == 200, results.text
    items = results.json()["results"]
    assert [item["seq"] for item in items] == [0, 1]
    assert {item["result"]["job_id"] for item in items} == set(job_ids)
    assert {item["result"]["status"] for item in items} == {"succeeded"}
    assert results.json()["next_cursor"] is None
    # keyset 分页：cursor 之后为空页。
    page2 = client.get(
        f"/api/workspaces/{ws}/job-batch-operations/{op_id}/results",
        params={"limit": 1},
    )
    assert len(page2.json()["results"]) == 1
    assert page2.json()["next_cursor"] == "0"
    tail = client.get(
        f"/api/workspaces/{ws}/job-batch-operations/{op_id}/results",
        params={"limit": 1, "cursor": 0},
    )
    assert [item["seq"] for item in tail.json()["results"]] == [1]


def test_large_batch_rerun_reports_per_job_results(client: TestClient, force_async: None) -> None:
    ws = _create_workspace(client)
    job_id = _create_job(client, "QR1")

    accepted = client.post(
        f"/api/workspaces/{ws}/jobs/batch-rerun",
        json={"job_ids": [job_id, "missing-job"], "node_key": "write_script"},
    )
    assert accepted.status_code == 202, accepted.text
    op_id = accepted.json()["operation"]["id"]

    _drain(client)

    body = client.get(f"/api/workspaces/{ws}/job-batch-operations/{op_id}").json()
    assert body["status"] == "completed"
    assert (body["succeeded_count"], body["failed_count"]) == (1, 1)
    assert body["reason_counts"] == {"not_found": 1}
    items = client.get(f"/api/workspaces/{ws}/job-batch-operations/{op_id}/results").json()[
        "results"
    ]
    by_job = {item["result"]["job_id"]: item["result"] for item in items}
    assert by_job[job_id]["status"] == "succeeded"
    assert by_job[job_id]["node_key"] == "write_script"
    assert by_job["missing-job"]["reason_code"] == "not_found"


def test_small_selection_stays_sync(client: TestClient) -> None:
    """默认阈值下小选择走同步快路径：200 + 逐 job 结果，无 operation 句柄。"""
    ws = _create_workspace(client)
    job_id = _create_job(client, "QS1")

    response = client.post(f"/api/workspaces/{ws}/jobs/batch-pause", json={"job_ids": [job_id]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert "operation" not in body
    assert [r["status"] for r in body["results"]] == ["succeeded"]


def test_empty_selection_keeps_sync_contract(client: TestClient, force_async: None) -> None:
    """空选择不入队：各端点的既有空选择契约原样（200 空结果 / 400）。"""
    ws = _create_workspace(client)

    rerun = client.post(
        f"/api/workspaces/{ws}/jobs/batch-rerun",
        json={"job_ids": [], "node_key": "write_script"},
    )
    assert rerun.status_code == 200
    assert rerun.json()["results"] == []

    upgrade = client.post(f"/api/workspaces/{ws}/jobs/batch-upgrade-workflow", json={"job_ids": []})
    assert upgrade.status_code == 400

    package = client.post(f"/api/workspaces/{ws}/jobs/package", json={"job_ids": []})
    assert package.status_code == 400

    clear = client.post(f"/api/workspaces/{ws}/jobs/clear-packed", json={"job_ids": []})
    assert clear.status_code == 400


def test_operation_reads_404_cross_workspace(client: TestClient, force_async: None) -> None:
    ws = _create_workspace(client)
    job_id = _create_job(client, "QX1")
    op_id = client.post(
        f"/api/workspaces/{ws}/jobs/batch-pause", json={"job_ids": [job_id]}
    ).json()["operation"]["id"]

    assert client.get(f"/api/workspaces/{ws}/job-batch-operations/nope").status_code == 404
    # 跨 workspace 引用同形 404（不泄漏存在性）。
    other = "batch-ops-other-ws"
    assert client.post("/api/workspaces", json={"id": other, "name": other}).status_code == 200
    assert client.get(f"/api/workspaces/{other}/job-batch-operations/{op_id}").status_code == 404
    assert (
        client.get(f"/api/workspaces/{other}/job-batch-operations/{op_id}/results").status_code
        == 404
    )


def test_async_dispatch_requires_auth(anon_client: TestClient) -> None:
    assert (
        anon_client.post(
            f"/api/workspaces/{WORKSPACE}/jobs/batch-pause", json={"job_ids": ["j1"]}
        ).status_code
        == 401
    )
    assert (
        anon_client.get(f"/api/workspaces/{WORKSPACE}/job-batch-operations/op-1").status_code == 401
    )
