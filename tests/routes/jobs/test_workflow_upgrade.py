from server.app.services.job_workflow_upgrade import JobWorkflowUpgradeService
from server.app.services.workflow_revisions import WorkflowRevisionService
from tests.helpers import load_demo_legacy_intake_definition, publish_legacy_intake_revision
from tests.helpers.auth import authenticate_client


def _create_workspace(
    client, name="default", default_workflow_key="education_video_problems_generation"
):
    workspace_id = client.post(
        "/api/workspaces", json={"id": default_workflow_key, "name": name}
    ).json()["workspace"]["id"]
    # The demo workflow no longer declares intake modes (#154); these tests
    # post job-batches, so publish the legacy-intake variant.
    publish_legacy_intake_revision(client.app.state.job_db, workspace_id)
    return workspace_id


def _create_job(client, workspace_id, question_id="Q301"):
    created = client.post(
        f"/api/workspaces/{workspace_id}/job-batches",
        json={
            "workflow_key": "education_video_problems_generation",
            "source_kind": "direct_ids",
            "knowledge_point_ids": [question_id],
        },
    ).json()
    return created["jobs"][0]["id"]


def _publish_next_revision(app, workspace_id):
    # Publish the legacy-intake variant (not the intake-less builtin) so jobs
    # created after the upgrade keep resolving direct_ids (#154).
    definition = load_demo_legacy_intake_definition()
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


def test_batch_upgrade_workflow_route_upgrades_filtered_jobs(tmp_path):
    from fastapi.testclient import TestClient

    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_a = _create_job(c, ws_id, question_id="Q401")
        job_b = _create_job(c, ws_id, question_id="Q402")
        excluded = _create_job(c, ws_id, question_id="Q403")
        current = _publish_next_revision(app, ws_id)
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-upgrade-workflow",
            json={"filter": {"status": "pending"}, "exclude_ids": [excluded]},
        )
        excluded_detail = c.get(f"/api/jobs/{excluded}").json()

    assert response.status_code == 200
    results = {r["job_id"]: r for r in response.json()["results"]}
    assert set(results) == {job_a, job_b}
    assert all(r["operation"] == "upgrade_workflow" for r in results.values())
    assert all(r["status"] == "succeeded" for r in results.values())
    assert excluded_detail["job"]["workflow_revision_id"] != current["id"]


def test_batch_upgrade_workflow_route_reports_per_job_results(tmp_path):
    from fastapi.testclient import TestClient

    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        stale = _create_job(c, ws_id, question_id="Q411")
        _publish_next_revision(app, ws_id)
        current = _create_job(c, ws_id, question_id="Q412")
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-upgrade-workflow",
            json={"job_ids": [stale, current, "missing-job"]},
        )

    assert response.status_code == 200
    results = {r["job_id"]: r for r in response.json()["results"]}
    assert results[stale]["status"] == "succeeded"
    assert results[current]["status"] == "skipped"
    assert results[current]["reason_code"] == "already_current"
    assert results["missing-job"]["status"] == "failed"
    assert results["missing-job"]["reason_code"] == "not_found"


def test_batch_upgrade_workflow_route_rejects_empty_selection(tmp_path):
    from fastapi.testclient import TestClient

    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        _create_job(c, ws_id)
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-upgrade-workflow",
            json={"filter": {"status": "failed"}, "exclude_ids": []},
        )

    assert response.status_code == 400


def test_batch_upgrade_workflow_route_validates_selection_shape(tmp_path):
    from fastapi.testclient import TestClient

    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        missing = c.post(f"/api/workspaces/{ws_id}/jobs/batch-upgrade-workflow", json={})
        both = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-upgrade-workflow",
            json={"job_ids": ["j1"], "filter": {"status": "pending"}},
        )

    assert missing.status_code == 422
    assert both.status_code == 422


def test_batch_upgrade_workflow_route_requires_workflows_enabled(tmp_path):
    from fastapi.testclient import TestClient

    app = _build_app(tmp_path)
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_id = _create_job(c, ws_id)
        app.state.settings.executor_runtime.workflows.enabled = False
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-upgrade-workflow",
            json={"job_ids": [job_id]},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Workflows are disabled"
