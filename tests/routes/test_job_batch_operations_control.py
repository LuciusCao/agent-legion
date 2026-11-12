"""Batch operation pause/resume/cancel HTTP state machine (#1397)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

WORKSPACE = "batch-op-control-ws"


@pytest.fixture
def workspace(client: TestClient) -> str:
    response = client.post("/api/workspaces", json={"id": WORKSPACE, "name": WORKSPACE})
    assert response.status_code == 200, response.text
    return WORKSPACE


def _insert(client: TestClient, workspace_id: str, op_id: str, status: str) -> None:
    job_db = client.app.state.job_db
    with job_db.connect() as conn:
        conn.execute(
            "insert into job_batch_operations(id, workspace_id, kind, status, items_total)"
            " values (%s, %s, 'pause', %s, 3)",
            (op_id, workspace_id, status),
        )


def _url(workspace_id: str, op_id: str, action: str) -> str:
    return f"/api/workspaces/{workspace_id}/job-batch-operations/{op_id}/{action}"


@pytest.mark.parametrize("status", ["pending", "running"])
def test_pause_success(client: TestClient, workspace: str, status: str) -> None:
    _insert(client, workspace, "op-pause", status)

    response = client.post(_url(workspace, "op-pause", "pause"))

    assert response.status_code == 200, response.text
    operation = response.json()
    assert operation["id"] == "op-pause"
    assert operation["kind"] == "pause"
    assert operation["status"] == "paused"
    assert operation["items_total"] == 3
    assert operation["processed_count"] == 0
    assert operation["finished_at"] is None
    assert client.post(_url(workspace, "op-pause", "pause")).status_code == 409


def test_resume_success_and_invalid_transitions(client: TestClient, workspace: str) -> None:
    _insert(client, workspace, "op-resume", "paused")

    resumed = client.post(_url(workspace, "op-resume", "resume"))

    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "pending"
    assert resumed.json()["finished_at"] is None
    assert client.post(_url(workspace, "op-resume", "resume")).status_code == 409
    _insert(client, workspace, "op-resume-running", "running")
    assert client.post(_url(workspace, "op-resume-running", "resume")).status_code == 409


@pytest.mark.parametrize("status", ["pending", "running", "paused"])
def test_cancel_success_and_repeat_conflict(
    client: TestClient, workspace: str, status: str
) -> None:
    _insert(client, workspace, "op-cancel", status)

    cancelled = client.post(_url(workspace, "op-cancel", "cancel"))

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["finished_at"] is not None
    assert client.post(_url(workspace, "op-cancel", "cancel")).status_code == 409
    assert client.post(_url(workspace, "op-cancel", "pause")).status_code == 409
    assert client.post(_url(workspace, "op-cancel", "resume")).status_code == 409


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
def test_terminal_transitions_conflict(
    client: TestClient, workspace: str, action: str, status: str
) -> None:
    _insert(client, workspace, "op-terminal", status)
    assert client.post(_url(workspace, "op-terminal", action)).status_code == 409


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
def test_unknown_and_cross_workspace_are_404(
    client: TestClient, workspace: str, action: str
) -> None:
    _insert(client, workspace, "op-private", "running")
    other = f"{workspace}-other"
    assert client.post("/api/workspaces", json={"id": other, "name": other}).status_code == 200

    assert client.post(_url(workspace, "missing", action)).status_code == 404
    assert client.post(_url(other, "op-private", action)).status_code == 404


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
def test_controls_require_auth(anon_client: TestClient, action: str) -> None:
    assert anon_client.post(_url(WORKSPACE, "op-1", action)).status_code == 401
