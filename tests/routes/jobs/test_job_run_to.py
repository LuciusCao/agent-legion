from tests.helpers import publish_legacy_intake_revision
from tests.helpers.auth import authenticate_client


def _create_workspace(
    client, name="default", default_workflow_key="education_video_problems_generation"
):
    workspace_id = client.post(
        "/api/workspaces", json={"name": name, "default_workflow_key": default_workflow_key}
    ).json()["workspace"]["id"]
    # The demo workflow no longer declares intake modes (#154); these tests
    # post job-batches, so publish the legacy-intake variant.
    publish_legacy_intake_revision(client.app.state.job_db, workspace_id)
    return workspace_id


def test_run_to_target_sets_execution_control(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q801"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(f"/api/jobs/{job_id}/run-to", json={"target_node_key": "publish_content"})
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["operation"] == "run_to"
    assert body["node_key"] == "publish_content"
    assert body["status"] == "succeeded"
    assert detail["job"]["execution_control"]["mode"] == "until_node"
    assert detail["job"]["execution_control"]["target_node_key"] == "publish_content"


def test_run_to_rejects_unknown_target(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q802"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(f"/api/jobs/{job_id}/run-to", json={"target_node_key": "missing_node"})

    assert response.status_code == 404
    assert "missing_node" in response.json()["detail"]


def test_run_to_rejects_start_outside_target_closure(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q803"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(
            f"/api/jobs/{job_id}/run-to",
            json={
                "target_node_key": "write_script",
                "start_node_key": "review_questions",
            },
        )

    assert response.status_code == 400
    assert "review_questions" in response.json()["detail"]


def test_continue_job_resumes_after_target_reached(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q804"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        job_db = app.state.job_db
        job_db.set_job_execution_target(job_id, "publish_content")
        job_db.pause_job(job_id, "target_reached")
        with job_db.connect() as conn:
            conn.execute("update jobs set status='paused' where id=%s", (job_id,))

        response = c.post(f"/api/jobs/{job_id}/continue", json={})
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["operation"] == "continue"
    assert body["status"] == "succeeded"
    assert detail["job"]["execution_control"]["mode"] == "full"
    assert detail["job"]["execution_control"]["target_node_key"] is None
