from tests.helpers.auth import authenticate_client
from tests.postgres_support import TEST_DATABASE_URL


def test_startup_does_not_seed_any_workspace(client):
    # Do not use `with client as c`: the fixture's client is the
    # worker-session shared TestClient whose lifespan is already running;
    # re-entering it would run the app lifespan a second time and its exit
    # would cancel the session's background tasks.
    response = client.get("/api/workspaces")

    assert response.status_code == 200
    assert response.json()["workspaces"] == []


def test_fresh_install_workspace_and_revision_flow(tmp_path):
    """Empty DB → bootstrap admin → create workspace → publish revision.

    No built-in workspace or workflow revision is seeded at startup; the whole
    chain must work through the explicit admin-driven paths only.
    """
    from fastapi.testclient import TestClient

    from server.app.main import create_app
    from server.app.services.workflow_revisions import WorkflowRevisionService
    from tests.helpers import load_builtin_definition

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        assert c.get("/api/workspaces").json()["workspaces"] == []
        created = c.post(
            "/api/workspaces",
            json={
                "name": "Comprehension",
                "default_workflow_key": "education_video_problems_generation",
            },
        )
        workspace_id = created.json()["workspace"]["id"]

        definition = load_builtin_definition("education_video_problems_generation")
        revision = WorkflowRevisionService(app.state.job_db).publish_workspace_revision(
            workspace_id, definition
        )
        active = c.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")

    assert created.status_code == 200
    assert revision["status"] == "active"
    assert active.status_code == 200
    assert active.json()["revision"]["id"] == revision["id"]


def test_create_workspace_and_scoped_jobs_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        workspace_response = c.post(
            "/api/workspaces",
            json={
                "name": "Math Sprint",
                "default_workflow_key": "education_video_problems_generation",
            },
        )
        workspace_id = workspace_response.json()["workspace"]["id"]
        other_response = c.post(
            "/api/workspaces",
            json={"name": "Other", "default_workflow_key": "education_video_problems_generation"},
        )
        other_id = other_response.json()["workspace"]["id"]
        created = c.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q001"],
            },
        )
        workspace_jobs = c.get(
            f"/api/workspaces/{workspace_id}/jobs?workflow_key=education_video_problems_generation"
        )
        other_jobs = c.get(
            f"/api/workspaces/{other_id}/jobs?workflow_key=education_video_problems_generation"
        )

    assert workspace_response.status_code == 200
    assert workspace_id == "math_sprint"
    assert created.status_code == 200
    body = created.json()
    assert body["jobs"][0]["workspace_id"] == workspace_id
    assert body["jobs"][0]["id"] == f"{workspace_id}_education_video_problems_generation_Q001"
    assert body["jobs"][0]["source_type"] == "question"
    assert [job["id"] for job in workspace_jobs.json()["jobs"]] == [body["jobs"][0]["id"]]
    assert other_jobs.json()["jobs"] == []


def test_delete_workspace_hidden_when_workflows_disabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = False
    with authenticate_client(TestClient(app)) as c:
        response = c.delete("/api/workspaces/some_ws")
    assert response.status_code == 404


def test_delete_workspace_named_default_is_allowed(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={"name": "default", "default_workflow_key": "education_video_problems_generation"},
        ).json()
        ws_id = ws["workspace"]["id"]
        response = c.delete(f"/api/workspaces/{ws_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] == ws_id


def test_delete_workspace_rejects_when_jobs_running(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={
                "name": "Running WS",
                "default_workflow_key": "education_video_problems_generation",
            },
        ).json()
        ws_id = ws["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q501"],
            },
        )
        job_db = app.state.job_db
        job_id = f"{ws_id}_education_video_problems_generation_Q501"
        job_db.update_job_status(job_id, "running")
        response = c.delete(f"/api/workspaces/{ws_id}")

    assert response.status_code == 400
    assert "running" in response.json()["detail"].lower()


def test_delete_workspace_cascades_and_returns_deleted_id(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws = c.post(
            "/api/workspaces",
            json={
                "name": "Delete Me",
                "default_workflow_key": "education_video_problems_generation",
            },
        ).json()
        ws_id = ws["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "education_video_problems_generation",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q601"],
            },
        )
        job_db = app.state.job_db
        job_id = f"{ws_id}_education_video_problems_generation_Q601"
        run = job_db.start_node_run(job_id, "write_script", ["echo", "hi"], "/dev/null")
        job_db.finish_node_run(run["id"], "completed", 0, "")
        job_ids = [j["id"] for j in job_db.list_jobs(workspace_id=ws_id)]
        response = c.delete(f"/api/workspaces/{ws_id}")
        get_response = c.get(f"/api/workspaces/{ws_id}")

    assert response.status_code == 200
    assert response.json()["deleted"] == ws_id
    assert get_response.status_code == 404
    with job_db._connect_read() as conn:
        assert (
            conn.execute("select 1 from job_batches where workspace_id = %s", (ws_id,)).fetchone()
            is None
        )
        assert (
            conn.execute("select 1 from jobs where workspace_id = %s", (ws_id,)).fetchone() is None
        )
        for job_id in job_ids:
            assert (
                conn.execute("select 1 from job_nodes where job_id = %s", (job_id,)).fetchone()
                is None
            )
            assert (
                conn.execute("select 1 from node_runs where job_id = %s", (job_id,)).fetchone()
                is None
            )


def test_delete_workspace_returns_404_for_unknown_workspace(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        response = c.delete("/api/workspaces/nonexistent")
    assert response.status_code == 404


def test_create_workspace_blank_mode_skips_demo_seed(tmp_path):
    """workflow_mode='blank': workspace row keeps the default_workflow_key slot,
    but no active revision and no factory Agent templates are seeded."""
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        created = c.post(
            "/api/workspaces",
            json={
                "name": "Blank WS",
                "default_workflow_key": "education_video_problems_generation",
                "workflow_mode": "blank",
            },
        )
        workspace = created.json()["workspace"]
        workspace_id = workspace["id"]
        active = c.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
        agents = c.get("/api/agent-definitions", params={"workspace_id": workspace_id})

    assert created.status_code == 200
    assert workspace["default_workflow_key"] == "education_video_problems_generation"
    assert active.status_code == 404
    assert agents.status_code == 200
    assert agents.json()["agents"] == []


def test_create_workspace_demo_mode_remains_default(tmp_path):
    """Default (and explicit 'demo') creation keeps seeding the active revision
    plus the factory Agent templates — zero behavior change."""
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        for payload in (
            {"name": "Demo Default", "default_workflow_key": "education_video_problems_generation"},
            {
                "name": "Demo Explicit",
                "default_workflow_key": "education_video_problems_generation",
                "workflow_mode": "demo",
            },
        ):
            created = c.post("/api/workspaces", json=payload)
            workspace_id = created.json()["workspace"]["id"]
            active = c.get(f"/api/workspaces/{workspace_id}/workflow-revisions/active")
            agents = c.get("/api/agent-definitions", params={"workspace_id": workspace_id})
            assert created.status_code == 200
            assert active.status_code == 200
            assert active.json()["revision"]["version"] == 1
            assert agents.json()["agents"] != []


def test_create_workspace_rejects_unknown_workflow_mode(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        response = c.post(
            "/api/workspaces",
            json={
                "name": "Bad Mode",
                "default_workflow_key": "education_video_problems_generation",
                "workflow_mode": "custom",
            },
        )
    assert response.status_code == 422


def test_create_workspace_stores_default_entity_and_intake_config(tmp_path):
    from server.app.jobs import JobQueries

    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "Intake WS",
        default_entity="knowledge",
        intake_config={"allowed_entities": ["question", "knowledge"]},
        default_workflow_key="education_video_problems_generation",
    )

    assert workspace["default_entity"] == "knowledge"
    assert workspace["intake_config"] == {"allowed_entities": ["question", "knowledge"]}


def test_create_workspace_uses_default_entity_and_intake_config_defaults(tmp_path):
    from server.app.jobs import JobQueries

    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    workspace = queries.create_workspace(
        "Default WS", default_workflow_key="education_video_problems_generation"
    )

    assert workspace["default_entity"] == "question"
    assert workspace["intake_config"] == {}


def test_update_workspace_persists_default_entity_and_intake_config(tmp_path):
    from server.app.jobs import JobQueries

    db_path = TEST_DATABASE_URL
    queries = JobQueries(db_path, tmp_path / "jobs")
    created = queries.create_workspace(
        "Update WS", default_workflow_key="education_video_problems_generation"
    )
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
    with authenticate_client(TestClient(app)) as c:
        response = c.post(
            "/api/workspaces",
            json={
                "name": "Intake WS",
                "default_workflow_key": "education_video_problems_generation",
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
    with authenticate_client(TestClient(app)) as c:
        created = c.post(
            "/api/workspaces",
            json={
                "name": "Update Intake",
                "default_workflow_key": "education_video_problems_generation",
            },
        ).json()
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
