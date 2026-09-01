"""Approval decision API: verdicts, guards and history (EXEC-APPROVAL-001)."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from server.app.workflows.definition import workflow_definition_from_mapping
from tests.helpers.auth import authenticate_client

APPROVAL_DAG = {
    "key": "placeholder",
    "label": "Approval Demo",
    "schema_version": 2,
    "nodes": {
        "entry": {"type": "start", "label": "入口"},
        "write": {"label": "写稿", "capability": "write_script", "outputs": ["script.md"]},
        "gate": {
            "type": "approval",
            "label": "逐字稿审批",
            "inputs": ["script.md"],
            "config": {"rework_target": "write"},
        },
        "publish": {
            "label": "发布",
            "capability": "publish_content",
            "inputs": ["script.md"],
            "terminal": {"outcome": "published"},
        },
    },
    "edges": [
        {"from": "entry", "to": "write"},
        {"from": "write", "to": "gate"},
        {"from": "gate", "to": "publish"},
    ],
}


def _setup(client: TestClient, tmp_path) -> tuple[str, str]:
    from server.app.services.workflow_revisions import WorkflowRevisionService

    workspace_id = client.post(
        "/api/workspaces", json={"id": "approval-api-ws", "name": "审批"}
    ).json()["workspace"]["id"]
    job_db = client.app.state.job_db
    definition = replace(workflow_definition_from_mapping(APPROVAL_DAG), key=workspace_id)
    WorkflowRevisionService(job_db).publish_workspace_revision(workspace_id, definition)
    job = job_db.create_job(
        workflow_key=workspace_id,
        source_type="material",
        source_id="chapter-1",
        run_id="",
        title="第一章",
        node_keys=list(definition.executable_nodes),
        workspace_id=workspace_id,
    )
    job_id = str(job["id"])
    with job_db.connect() as conn:
        conn.execute(
            "update job_nodes set status='completed' where job_id=%s and node_key='write'",
            (job_id,),
        )
        conn.execute(
            "update job_nodes set status='awaiting_approval' where job_id=%s and node_key='gate'",
            (job_id,),
        )
    return workspace_id, job_id


def _make_app(tmp_path):
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    return app


def test_approve_then_history(tmp_path):
    with authenticate_client(TestClient(_make_app(tmp_path))) as c:
        ws_id, job_id = _setup(c, tmp_path)
        url = f"/api/workspaces/{ws_id}/jobs/{job_id}/nodes/gate/approval"
        response = c.post(url, json={"verdict": "approved", "note": "结构OK"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["verdict"] == "approved"
        assert body["decided_by"].startswith("user:")

        # Double-decide conflicts: the gate is no longer awaiting.
        assert c.post(url, json={"verdict": "approved"}).status_code == 409

        history = c.get(f"/api/workspaces/{ws_id}/jobs/{job_id}/approvals").json()
        assert [d["verdict"] for d in history["decisions"]] == ["approved"]

        detail = c.get(f"/api/jobs/{job_id}").json()
        assert detail["job"]["status"] == "queued"


def test_rework_requires_note_and_resets_upstream(tmp_path):
    with authenticate_client(TestClient(_make_app(tmp_path))) as c:
        ws_id, job_id = _setup(c, tmp_path)
        url = f"/api/workspaces/{ws_id}/jobs/{job_id}/nodes/gate/approval"
        assert c.post(url, json={"verdict": "rework", "note": ""}).status_code == 400

        response = c.post(url, json={"verdict": "rework", "note": "案例前置，合并二三节"})
        assert response.status_code == 200, response.text
        assert response.json()["rework_target"] == "write"

        job_db = c.app.state.job_db
        assert job_db.get_job_node(job_id, "write")["status"] == "pending"
        assert job_db.get_job_node(job_id, "gate")["status"] == "stale"


def test_reject_fails_job(tmp_path):
    with authenticate_client(TestClient(_make_app(tmp_path))) as c:
        ws_id, job_id = _setup(c, tmp_path)
        url = f"/api/workspaces/{ws_id}/jobs/{job_id}/nodes/gate/approval"
        response = c.post(url, json={"verdict": "rejected", "note": "素材不合格"})
        assert response.status_code == 200, response.text
        detail = c.get(f"/api/jobs/{job_id}").json()
        assert detail["job"]["status"] == "failed"


def test_decision_on_non_approval_node_is_404(tmp_path):
    with authenticate_client(TestClient(_make_app(tmp_path))) as c:
        ws_id, job_id = _setup(c, tmp_path)
        url = f"/api/workspaces/{ws_id}/jobs/{job_id}/nodes/write/approval"
        assert c.post(url, json={"verdict": "approved"}).status_code == 404
