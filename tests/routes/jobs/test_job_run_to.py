def test_run_to_target_sets_execution_control(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q801"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(
            f"/api/jobs/{job_id}/run-to", json={"target_node_key": "question_understanding"}
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["operation"] == "run_to"
    assert body["node_key"] == "question_understanding"
    assert body["status"] == "succeeded"
    assert detail["job"]["execution_control"]["mode"] == "until_node"
    assert detail["job"]["execution_control"]["target_node_key"] == "question_understanding"


def test_run_to_rejects_unknown_target(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q802"],
                "knowledge_codes": [],
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
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q803"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(
            f"/api/jobs/{job_id}/run-to",
            json={"target_node_key": "question_understanding", "start_node_key": "content_review"},
        )

    assert response.status_code == 400
    assert "content_review" in response.json()["detail"]


def test_continue_job_resumes_after_target_reached(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q804"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        job_db = app.state.job_db
        job_db.set_job_execution_target(job_id, "question_understanding")
        job_db.pause_job(job_id, "target_reached")
        with job_db.connect() as conn:
            conn.execute("update jobs set status='paused' where id=?", (job_id,))

        response = c.post(f"/api/jobs/{job_id}/continue", json={})
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["operation"] == "continue"
    assert body["status"] == "succeeded"
    assert detail["job"]["execution_control"]["mode"] == "full"
    assert detail["job"]["execution_control"]["target_node_key"] is None
