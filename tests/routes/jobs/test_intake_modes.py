import json

from tests.helpers import publish_legacy_intake_revision
from tests.helpers.auth import authenticate_client


def _create_workspace(client, name="default", default_workflow_key="test"):
    workspace_id = client.post(
        "/api/workspaces", json={"id": default_workflow_key, "name": name}
    ).json()["workspace"]["id"]
    # The demo workflow no longer declares intake modes (#154); these tests
    # post job-batches, so publish the legacy-intake variant.
    publish_legacy_intake_revision(client.app.state.job_db, workspace_id)
    return workspace_id


def test_workspace_batch_delete_removes_jobs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "test",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q604"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.request(
            "DELETE",
            f"/api/workspaces/{ws_id}/jobs/batch",
            json={"job_ids": [job_id]},
        )
        detail = c.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["job_id"] == job_id
    assert results[0]["operation"] == "delete"
    assert results[0]["status"] == "succeeded"
    assert detail.status_code == 404


def test_workspace_default_entity_is_used_when_batch_omits_entity(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        workspace_response = c.post(
            "/api/workspaces",
            json={
                "id": "education_video_problems_generation",
                "name": "question-default",
                "default_entity": "question",
            },
        )
        workspace_id = workspace_response.json()["workspace"]["id"]
        publish_legacy_intake_revision(c.app.state.job_db, workspace_id)

        response = c.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "workflow_key": workspace_id,
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q001"],
            },
        )
        body = response.json()

    assert response.status_code == 200
    assert body["jobs"][0]["source_type"] == "question"
    # The entity lands on the job's input document (RUN-FREEZE-001).
    input_doc = json.loads(body["jobs"][0]["input_json"])
    assert input_doc["entity_type"] == "question"


def test_batch_with_entity_question(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "test",
                "entity": "question",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q001", "Q002"],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["source_id"] for job in body["jobs"]] == ["Q001", "Q002"]
    inputs = [json.loads(job["input_json"]) for job in body["jobs"]]
    assert [doc["entity_type"] for doc in inputs] == ["question", "question"]
    assert [doc["external_id"] for doc in inputs] == ["Q001", "Q002"]


def test_batch_unsupported_entity_mode(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "test",
                "entity": "knowledge",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["K001"],
            },
        )

    assert response.status_code == 400
    assert "Unsupported entity and intake mode combination" in response.json()["detail"]


def test_batch_video_entity_direct_ids_is_resolver_driven(tmp_path):
    """The intake no longer special-cases the video entity: any registered
    (entity, mode) resolver applies to every workflow."""
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "test",
                "entity": "video",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["K001"],
            },
        )

    # ("video", "direct_ids") is a registered direct resolver: accepted.
    assert response.status_code == 200
    assert response.json()["created_count"] == 1


def test_batch_unregistered_entity_mode_combination_rejected(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "test",
                "entity": "audio",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["K001"],
            },
        )

    assert response.status_code == 400
    assert "Unsupported entity and intake mode combination" in response.json()["detail"]


def test_batch_with_entity_question_direct_ids(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "test",
                "entity": "question",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q1", "Q2"],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["source_id"] for job in body["jobs"]] == ["Q1", "Q2"]
    assert [job["title"] for job in body["jobs"]] == ["Question Q1", "Question Q2"]
    inputs = [json.loads(job["input_json"]) for job in body["jobs"]]
    assert [doc["external_id"] for doc in inputs] == ["Q1", "Q2"]
    assert [doc["entity_type"] for doc in inputs] == ["question", "question"]


def test_workflow_response_no_task_entity(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        created = c.post("/api/workspaces", json={"id": "test", "name": "Intake Shape"})
        assert created.status_code == 200, created.text
        workspace_id = created.json()["workspace"]["id"]
        publish_legacy_intake_revision(c.app.state.job_db, workspace_id)
        response = c.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")

    assert response.status_code == 200
    body = response.json()
    for mode in body["workflow"]["intake"]["modes"]:
        assert "task_entity" not in mode
        assert "resolver" not in mode
        assert "resource" not in mode
        assert "key" in mode
        assert "label" in mode
        assert "input_field" in mode


def test_batch_delete_skips_not_found_and_running_jobs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        c.post(
            "/api/workspaces",
            json={"id": "test", "name": "Test"},
        )
        publish_legacy_intake_revision(c.app.state.job_db, "test")
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "test",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q1"],
            },
        )
        # Delete non-existent job
        resp = c.request(
            "DELETE",
            "/api/workspaces/test/jobs/batch",
            json={"job_ids": ["nonexistent"]},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(r["status"] == "failed" and r["reason_code"] == "not_found" for r in results)


def test_batch_delete_skips_running_job(tmp_path):

    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        c.post(
            "/api/workspaces",
            json={"id": "test", "name": "Test"},
        )
        publish_legacy_intake_revision(c.app.state.job_db, "test")
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "test",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q1"],
            },
        )
        job_id = "test_test_Q1"
        log_dir = app.state.settings.logs_dir / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}-intake_knowledge_points.log"
        log_path.write_text("running")
        app.state.job_db.start_node_run(job_id, "intake_knowledge_points", ["cmd"], str(log_path))
        resp = c.request(
            "DELETE",
            "/api/workspaces/test/jobs/batch",
            json={"job_ids": [job_id]},
        )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(
        r["status"] == "failed"
        and r["reason_code"] == "busy"
        and "running" in (r.get("message") or "").lower()
        for r in results
    )


def test_batch_run_to_returns_results_in_order(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "test",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q805"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-run-to",
            json={"job_ids": [job_id, "missing-job"], "target_node_key": "publish_content"},
        )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert results[0]["job_id"] == job_id
    assert results[0]["status"] == "succeeded"
    assert results[1]["job_id"] == "missing-job"
    assert results[1]["status"] == "failed"
