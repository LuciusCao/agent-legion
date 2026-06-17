from pathlib import Path


def test_rerun_node_marks_downstream_stale(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q201"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(f"/api/jobs/{job_id}/nodes/question_understanding/rerun")
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["node_key"] == "question_understanding"
    assert body["operation"] == "rerun"
    assert body["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["question_understanding"] == "pending"
    assert nodes["misconception_analysis"] == "stale"
    assert nodes["natural_language_reading"] == "stale"
    assert nodes["solution_decomposition"] == "stale"
    assert nodes["faq_generation"] == "stale"
    assert nodes["content_graph_generation"] == "stale"
    assert nodes["interactive_template_generation"] == "stale"
    assert nodes["content_review"] == "stale"
    assert nodes["assemble_package"] == "stale"


def test_workspace_batch_rerun_marks_jobs_queued(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        created = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q603"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        app.state.job_db.update_job_status(job_id, "failed", "boom")
        response = c.post(
            "/api/workspaces/default/jobs/batch-rerun",
            json={"job_ids": [job_id], "node_key": "fetch_question_context"},
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "job_id": job_id,
            "operation": "rerun",
            "status": "succeeded",
            "node_key": "fetch_question_context",
            "reason_code": None,
            "message": None,
        }
    ]
    assert detail["job"]["status"] == "queued"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["fetch_question_context"] == "pending"
    assert nodes["question_understanding"] == "stale"


def test_batch_rerun_skips_not_found_and_running_jobs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )
        # Rerun non-existent job
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={"job_ids": ["nonexistent"], "node_key": "fetch_question_context"},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(r["status"] == "failed" and r["reason_code"] == "not_found" for r in results)


def test_rerun_node_errors(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_content_Q1"

        # Job not found
        resp = c.post("/api/jobs/nonexistent/nodes/fetch_question_context/rerun")
        assert resp.status_code == 404

        # Node not found
        resp = c.post(f"/api/jobs/{job_id}/nodes/nonexistent/rerun")
        assert resp.status_code == 404


def test_rerun_node_rejects_running_job(tmp_path):

    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_content_Q1"
        log_dir = app.state.settings.logs_dir / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}-fetch_question_context.log"
        log_path.write_text("running")
        app.state.job_db.start_node_run(job_id, "fetch_question_context", ["cmd"], str(log_path))
        resp = c.post(f"/api/jobs/{job_id}/nodes/fetch_question_context/rerun")
    assert resp.status_code == 400
    assert "running" in resp.json()["detail"].lower()


def test_rerun_node_cleanup_failed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_content_Q1"

        def _fail_cleanup(*args, **kwargs):
            raise ValueError("cannot remove artifact")

        monkeypatch.setattr(
            "server.app.services.job_artifact_mutation.JobArtifactMutationService.stage_outputs",
            _fail_cleanup,
        )
        resp = c.post(f"/api/jobs/{job_id}/nodes/fetch_question_context/rerun")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cannot remove artifact"


def test_rerun_node_mark_for_rerun_value_error(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_content_Q1"

        def _fail_mark(*args, **kwargs):
            raise ValueError("invalid node state")

        monkeypatch.setattr(
            "server.app.jobs.atomic_mutations.AtomicJobMutationsMixin."
            "mark_nodes_for_rerun_in_transaction",
            _fail_mark,
        )

        resp = c.post(f"/api/jobs/{job_id}/nodes/fetch_question_context/rerun")
    assert resp.status_code == 400
    assert "invalid node state" in resp.json()["detail"].lower()


def test_rerun_node_preserves_ancestors(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q700"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        app.state.job_db.update_job_node(job_id, "fetch_question_context", status="completed")
        c.post(f"/api/jobs/{job_id}/nodes/question_understanding/rerun")
        detail = c.get(f"/api/jobs/{job_id}").json()

    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["fetch_question_context"] == "completed"
    assert nodes["question_understanding"] == "pending"


def test_batch_rerun_node_not_found_for_one_job(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q701"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_content_Q701"
        # Remove downstream nodes so the selected node is absent from this job.
        job_db = app.state.job_db
        with job_db.connect() as conn:
            conn.execute(
                "delete from job_nodes where job_id=? and node_key=?",
                (job_id, "question_understanding"),
            )
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={"job_ids": [job_id], "node_key": "question_understanding"},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["reason_code"] == "node_not_found"


def test_batch_rerun_mixed_pipelines(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q702"],
                "knowledge_codes": [],
            },
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "reading_analysis",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q702"],
            },
        )
        q_job_id = "test_question_content_Q702"
        r_job_id = "test_reading_analysis_Q702"
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={"job_ids": [q_job_id, r_job_id], "node_key": "question_understanding"},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["job_id"] == q_job_id
    assert results[0]["status"] == "succeeded"
    assert results[1]["job_id"] == r_job_id
    assert results[1]["status"] == "failed"
    assert results[1]["reason_code"] == "node_not_found"


def test_batch_rerun_request_order_preserved(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q703", "Q704"],
                "knowledge_codes": [],
            },
        )
        first = "test_question_content_Q703"
        second = "test_question_content_Q704"
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={
                "job_ids": [second, first],
                "node_key": "question_understanding",
            },
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["job_id"] for r in results] == [second, first]
    assert all(r["status"] == "succeeded" for r in results)


def test_rerun_node_rejects_active_lease(tmp_path):
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    from server.app.executors._lease_transactions import _sqlite_timestamp
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q705"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_content_Q705"
        job_db = app.state.job_db
        run = job_db.start_node_run(job_id, "question_understanding", ["cmd"], "/dev/null")
        now = datetime.now(UTC)
        with job_db.connect() as conn:
            conn.execute(
                """
                insert into executor_leases(
                    id, execution_id, executor_id, workspace_id, job_id, workflow_key,
                    node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    "lease-1",
                    "exec-1",
                    "local-default",
                    "test",
                    job_id,
                    "question_content",
                    "question_understanding",
                    run["id"],
                    _sqlite_timestamp(now),
                    _sqlite_timestamp(now),
                    _sqlite_timestamp(now + timedelta(seconds=300)),
                ),
            )
        resp = c.post(f"/api/jobs/{job_id}/nodes/question_understanding/rerun")

    assert resp.status_code == 400
    assert "active" in resp.json()["detail"].lower()


def test_rerun_node_expired_lease_not_blocking(tmp_path):
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    from server.app.executors._lease_transactions import _sqlite_timestamp
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q706"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_content_Q706"
        job_db = app.state.job_db
        run = job_db.start_node_run(job_id, "question_understanding", ["cmd"], "/dev/null")
        job_db.finish_node_run(run["id"], "failed", 1, "expired")
        now = datetime.now(UTC)
        with job_db.connect() as conn:
            conn.execute(
                """
                insert into executor_leases(
                    id, execution_id, executor_id, workspace_id, job_id, workflow_key,
                    node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    "lease-1",
                    "exec-1",
                    "local-default",
                    "test",
                    job_id,
                    "question_content",
                    "question_understanding",
                    run["id"],
                    _sqlite_timestamp(now),
                    _sqlite_timestamp(now),
                    _sqlite_timestamp(now - timedelta(seconds=1)),
                ),
            )
        resp = c.post(f"/api/jobs/{job_id}/nodes/question_understanding/rerun")
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["question_understanding"] == "pending"


def test_rerun_node_rollback_on_db_failure(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q707"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_content_Q707"
        storage = Path(app.state.job_db.get_job(job_id)["storage_dir"])
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "understanding.json").write_text("understanding")

        def _fail(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "server.app.jobs.atomic_mutations.AtomicJobMutationsMixin."
            "mark_nodes_for_rerun_in_transaction",
            _fail,
        )
        resp = c.post(f"/api/jobs/{job_id}/nodes/question_understanding/rerun")

    assert resp.status_code == 400
    assert (storage / "understanding.json").read_text() == "understanding"
