import json

from tests.helpers.auth import authenticate_client


def _create_workspace(client, name="default", default_workflow_key="question_comprehension_info"):
    return client.post(
        "/api/workspaces", json={"name": name, "default_workflow_key": default_workflow_key}
    ).json()["workspace"]["id"]


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
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q604"],
                "knowledge_codes": [],
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


def test_workspace_intake_config_rejects_disabled_mode(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        workspace_response = c.post(
            "/api/workspaces",
            json={
                "name": "intake-filtered",
                "default_workflow_key": "question_comprehension_info",
                "intake_config": {"enabled_modes": ["batch_by_ids"]},
            },
        )
        workspace_id = workspace_response.json()["workspace"]["id"]

        response = c.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K001"],
            },
        )

    assert response.status_code == 400
    assert "Intake mode is disabled" in response.json()["detail"]


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
                "name": "question-default",
                "default_workflow_key": "question_comprehension_info",
                "default_entity": "question",
                "intake_config": {"enabled_modes": ["batch_by_ids"]},
            },
        )
        workspace_id = workspace_response.json()["workspace"]["id"]

        response = c.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
            },
        )
        body = response.json()
        batch = app.state.job_db.get_batch(body["batch"]["id"])

    assert response.status_code == 200
    assert body["jobs"][0]["source_type"] == "question"
    payload = json.loads(batch["source_payload_json"])
    assert payload["entity"] == "question"


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
                "workflow_key": "question_comprehension_info",
                "entity": "question",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001", "Q002"],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["source_id"] for job in body["jobs"]] == ["Q001", "Q002"]
    payload = json.loads(body["batch"]["source_payload_json"])
    assert payload["entity"] == "question"
    assert payload["question_ids"] == ["Q001", "Q002"]


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
                "workflow_key": "question_comprehension_info",
                "entity": "knowledge",
                "source_kind": "batch_by_ids",
                "question_ids": ["K001"],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 400
    assert "Unsupported entity and intake mode combination" in response.json()["detail"]


def test_batch_video_entity_is_unsupported_for_question_comprehension_workflow(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "entity": "video",
                "source_kind": "batch_by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K001"],
            },
        )

    assert response.status_code == 400
    assert "Unsupported entity and intake mode combination" in response.json()["detail"]


def test_batch_with_entity_question_batch_by_ids(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "entity": "question",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q1", "Q2"],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["source_id"] for job in body["jobs"]] == ["Q1", "Q2"]
    assert [job["title"] for job in body["jobs"]] == ["Q1", "Q2"]
    payload = json.loads(body["batch"]["source_payload_json"])
    assert payload["entity"] == "question"
    assert payload["question_ids"] == ["Q1", "Q2"]
    assert [c["entity_id"] for c in payload["task_candidates"]] == ["Q1", "Q2"]
    assert [c["entity_type"] for c in payload["task_candidates"]] == ["question", "question"]


def test_workflow_response_no_task_entity(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/workflows/question_comprehension_info")

    assert response.status_code == 200
    body = response.json()
    for mode in body["workflow"]["intake"]["modes"]:
        assert "task_entity" not in mode
        assert "resolver" not in mode
        assert "key" in mode
        assert "label" in mode
        assert "input_field" in mode
        assert "resource" in mode


def test_batch_delete_skips_not_found_and_running_jobs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        c.post(
            "/api/workspaces",
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
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
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_comprehension_info_Q1"
        log_dir = app.state.settings.logs_dir / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}-fetch_questions.log"
        log_path.write_text("running")
        app.state.job_db.start_node_run(job_id, "fetch_questions", ["cmd"], str(log_path))
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
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q805"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-run-to",
            json={"job_ids": [job_id, "missing-job"], "target_node_key": "review_key_info"},
        )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert results[0]["job_id"] == job_id
    assert results[0]["status"] == "succeeded"
    assert results[1]["job_id"] == "missing-job"
    assert results[1]["status"] == "failed"
