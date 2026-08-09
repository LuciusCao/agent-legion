from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_builtin_definition
from tests.helpers.auth import authenticate_client


def _create_workspace(client, name="default", default_workflow_key="question_comprehension_info"):
    return client.post(
        "/api/workspaces", json={"name": name, "default_workflow_key": default_workflow_key}
    ).json()["workspace"]["id"]


def _create_job(client, workspace_id, question_id="Q301"):
    created = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": "question_comprehension_info",
            "source_kind": "batch_by_ids",
            "question_ids": [question_id],
            "knowledge_codes": [],
        },
    ).json()
    return created["jobs"][0]["id"]


def _publish_next_revision(app, workspace_id):
    definition = load_builtin_definition("question_comprehension_info")
    return WorkflowRevisionService(app.state.job_db).publish_workspace_revision(
        workspace_id, definition
    )


def _build_app(tmp_path, *, workflows_enabled=True):
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = workflows_enabled
    return app


def test_upgrade_workflow_route_upgrades_stale_job(tmp_path):
    from fastapi.testclient import TestClient

    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id)
        current = _publish_next_revision(app, ws_id)
        response = c.post(f"/api/jobs/{job_id}/upgrade-workflow")
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["operation"] == "upgrade_workflow"
    assert body["status"] == "succeeded"
    assert detail["job"]["workflow_revision_id"] == current["id"]
    assert detail["job"]["workflow_version"] == current["version"]
    assert detail["job"]["status"] == "queued"
    assert detail["job"]["is_workflow_outdated"] is False


def test_upgrade_workflow_route_rejects_already_current_job(tmp_path):
    from fastapi.testclient import TestClient

    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id)
        response = c.post(f"/api/jobs/{job_id}/upgrade-workflow")

    assert response.status_code == 400
    assert response.json()["detail"] == "Job is already current"


def test_upgrade_workflow_route_returns_404_for_missing_job(tmp_path):
    from fastapi.testclient import TestClient

    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        response = c.post("/api/jobs/missing-job/upgrade-workflow")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"


def test_upgrade_workflow_route_requires_workflows_enabled(tmp_path):
    from fastapi.testclient import TestClient

    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id)
        app.state.settings.executor_runtime.workflows.enabled = False
        response = c.post(f"/api/jobs/{job_id}/upgrade-workflow")

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflows are disabled"


def test_upgrade_workflow_route_maps_service_not_found_to_404(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    def _not_found(self, workspace_id, job_id):
        return {
            "job_id": job_id,
            "operation": "upgrade_workflow",
            "status": "failed",
            "node_key": None,
            "reason_code": "not_found",
            "message": "Job not found",
        }

    monkeypatch.setattr(JobWorkflowUpgradeService, "upgrade", _not_found)
    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id)
        response = c.post(f"/api/jobs/{job_id}/upgrade-workflow")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job not found"
