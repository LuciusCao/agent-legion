def test_startup_creates_question_comprehension_workspace(client):
    with client as c:
        response = c.get("/api/workspaces")

    assert response.status_code == 200
    workspaces = response.json()["workspaces"]
    by_id = {workspace["id"]: workspace for workspace in workspaces}

    assert "question_comprehension" in by_id
    assert by_id["question_comprehension"]["name"] == "题目审题信息"
    assert by_id["question_comprehension"]["default_workflow_key"] == "question_comprehension_info"


def test_create_workspace_and_scoped_jobs_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        workspace_response = c.post("/api/workspaces", json={"name": "Math Sprint"})
        workspace_id = workspace_response.json()["workspace"]["id"]
        other_response = c.post("/api/workspaces", json={"name": "Other"})
        other_id = other_response.json()["workspace"]["id"]
        created = c.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
            },
        )
        workspace_jobs = c.get(f"/api/workspaces/{workspace_id}/jobs?workflow_key=question_content")
        other_jobs = c.get(f"/api/workspaces/{other_id}/jobs?workflow_key=question_content")

    assert workspace_response.status_code == 200
    assert workspace_id == "math_sprint"
    assert created.status_code == 200
    body = created.json()
    assert body["jobs"][0]["workspace_id"] == workspace_id
    assert body["jobs"][0]["id"] == f"{workspace_id}_question_content_Q001"
    assert body["jobs"][0]["source_type"] == "question"
    assert [job["id"] for job in workspace_jobs.json()["jobs"]] == [body["jobs"][0]["id"]]
    assert other_jobs.json()["jobs"] == []


def test_delete_workspace_hidden_when_workflows_disabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = False
    with TestClient(app) as c:
        response = c.delete("/api/workspaces/some_ws")
    assert response.status_code == 404


def test_delete_workspace_named_default_is_allowed(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws = c.post("/api/workspaces", json={"name": "default"}).json()
        ws_id = ws["workspace"]["id"]
        response = c.delete(f"/api/workspaces/{ws_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] == ws_id


def test_delete_workspace_rejects_when_jobs_running(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws = c.post("/api/workspaces", json={"name": "Running WS"}).json()
        ws_id = ws["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q501"],
                "knowledge_codes": [],
            },
        )
        job_db = app.state.job_db
        job_id = f"{ws_id}_question_content_Q501"
        job_db.update_job_status(job_id, "running")
        response = c.delete(f"/api/workspaces/{ws_id}")

    assert response.status_code == 400
    assert "running" in response.json()["detail"].lower()


def test_delete_workspace_cascades_and_returns_deleted_id(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws = c.post("/api/workspaces", json={"name": "Delete Me"}).json()
        ws_id = ws["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q601"],
                "knowledge_codes": [],
            },
        )
        job_db = app.state.job_db
        job_id = f"{ws_id}_question_content_Q601"
        run = job_db.start_node_run(job_id, "question_understanding", ["echo", "hi"], "/dev/null")
        job_db.finish_node_run(run["id"], "completed", 0, "")
        job_ids = [j["id"] for j in job_db.list_jobs(workspace_id=ws_id)]
        response = c.delete(f"/api/workspaces/{ws_id}")
        get_response = c.get(f"/api/workspaces/{ws_id}")

    assert response.status_code == 200
    assert response.json()["deleted"] == ws_id
    assert get_response.status_code == 404
    with job_db._connect_read() as conn:
        assert (
            conn.execute("select 1 from job_batches where workspace_id = ?", (ws_id,)).fetchone()
            is None
        )
        assert (
            conn.execute("select 1 from jobs where workspace_id = ?", (ws_id,)).fetchone() is None
        )
        for job_id in job_ids:
            assert (
                conn.execute("select 1 from job_nodes where job_id = ?", (job_id,)).fetchone()
                is None
            )
            assert (
                conn.execute("select 1 from node_runs where job_id = ?", (job_id,)).fetchone()
                is None
            )


def test_delete_workspace_returns_404_for_unknown_workspace(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        response = c.delete("/api/workspaces/nonexistent")
    assert response.status_code == 404


def test_create_workspace_stores_default_entity_and_intake_config(tmp_path):
    from server.app.jobs import JobQueries

    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "Intake WS",
        default_entity="knowledge",
        intake_config={"allowed_entities": ["question", "knowledge"]},
    )

    assert workspace["default_entity"] == "knowledge"
    assert workspace["intake_config"] == {"allowed_entities": ["question", "knowledge"]}


def test_create_workspace_uses_default_entity_and_intake_config_defaults(tmp_path):
    from server.app.jobs import JobQueries

    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace("Default WS")

    assert workspace["default_entity"] == "question"
    assert workspace["intake_config"] == {}


def test_update_workspace_persists_default_entity_and_intake_config(tmp_path):
    from server.app.jobs import JobQueries

    db_path = tmp_path / "jobs.sqlite"
    queries = JobQueries(db_path, tmp_path / "jobs")
    created = queries.create_workspace("Update WS")
    workspace_id = created["id"]
    workspace = queries.update_workspace(
        workspace_id,
        default_entity="knowledge",
        intake_config={"max_batch_size": 50},
    )
    fetched = queries.get_workspace(workspace_id)

    assert workspace["default_entity"] == "knowledge"
    assert workspace["intake_config"] == {"max_batch_size": 50}
    assert fetched["default_entity"] == "knowledge"
    assert fetched["intake_config"] == {"max_batch_size": 50}


def test_create_workspace_with_intake_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        response = c.post(
            "/api/workspaces",
            json={
                "name": "Intake WS",
                "default_entity": "knowledge",
                "intake_config": {"allowed_entities": ["question", "knowledge"]},
            },
        )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["default_entity"] == "knowledge"
    assert workspace["intake_config"] == {"allowed_entities": ["question", "knowledge"]}


def test_update_workspace_intake_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        created = c.post("/api/workspaces", json={"name": "Update Intake"}).json()
        workspace_id = created["workspace"]["id"]
        response = c.patch(
            f"/api/workspaces/{workspace_id}",
            json={
                "default_entity": "knowledge",
                "intake_config": {"max_batch_size": 50},
            },
        )
        fetched = c.get(f"/api/workspaces/{workspace_id}")

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["default_entity"] == "knowledge"
    assert workspace["intake_config"] == {"max_batch_size": 50}
    assert fetched.json()["workspace"]["default_entity"] == "knowledge"
    assert fetched.json()["workspace"]["intake_config"] == {"max_batch_size": 50}
