"""GET /workspaces/{id}/jobs guard coverage (#211 Phase 3)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers.auth import authenticate_client


def test_list_jobs_rejects_mismatched_workflow_key(tmp_path, job_db):
    """Subagent review P3-1 on #307: guard parity with failed-node-runs —
    the deprecated query param can no longer narrow (read binding); a
    mismatched key is rejected instead of silently widening the list."""
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    with authenticate_client(TestClient(app)) as client:
        job_db.create_workspace("ws-jobs-key", default_workflow_key="ws-jobs-key")

        mismatched = client.get("/api/workspaces/ws-jobs-key/jobs?workflow_key=other_flow")
        assert mismatched.status_code == 400, mismatched.text
        assert "workflow_key must equal the workspace id" in mismatched.json()["detail"]

        equal = client.get("/api/workspaces/ws-jobs-key/jobs?workflow_key=ws-jobs-key")
        assert equal.status_code == 200
        assert equal.json()["jobs"] == []
