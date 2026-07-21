from server.app.storage_paths import resolve_job_dir


def _create_workspace(client, name="default", default_workflow_key="question_comprehension_info"):
    return client.post(
        "/api/workspaces", json={"name": name, "default_workflow_key": default_workflow_key}
    ).json()["workspace"]["id"]


def test_rerun_node_marks_downstream_stale(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q201"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(f"/api/jobs/{job_id}/nodes/review_key_info/rerun")
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["node_key"] == "review_key_info"
    assert body["operation"] == "rerun"
    assert body["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["review_key_info"] == "pending"
    assert nodes["fetch_questions"] == "pending"
    assert nodes["clean_and_parse"] == "pending"
    assert nodes["generate_key_info"] == "pending"
    assert nodes["generate_possible_errors"] == "stale"
    assert nodes["review_possible_errors"] == "stale"
    assert nodes["assess_comprehension_difficulty"] == "stale"
    assert nodes["assemble_comprehension_info"] == "stale"


def test_workspace_batch_rerun_marks_jobs_queued(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q603"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        app.state.job_db.update_job_status(job_id, "failed", "boom")
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={"job_ids": [job_id], "node_key": "fetch_questions"},
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "job_id": job_id,
            "operation": "rerun",
            "status": "succeeded",
            "node_key": "fetch_questions",
            "reason_code": None,
            "message": None,
        }
    ]
    assert detail["job"]["status"] == "queued"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["fetch_questions"] == "pending"
    assert nodes["review_key_info"] == "stale"


def test_batch_rerun_skips_not_found_and_running_jobs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
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
        # Rerun non-existent job
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={"job_ids": ["nonexistent"], "node_key": "fetch_questions"},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(r["status"] == "failed" and r["reason_code"] == "not_found" for r in results)


def test_batch_rerun_from_failed_node(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q701"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        app.state.job_db.update_job_status(job_id, "failed", "boom")
        app.state.job_db.update_job_node(job_id, "clean_and_parse", status="failed")

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={"job_ids": [job_id], "from_failed_node": True},
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == [
        {
            "job_id": job_id,
            "operation": "rerun",
            "status": "succeeded",
            "node_key": "clean_and_parse",
            "reason_code": None,
            "message": None,
        }
    ]
    assert detail["job"]["status"] == "queued"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["clean_and_parse"] == "pending"


def test_batch_rerun_from_failed_node_skips_non_failed(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q702"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={"job_ids": [job_id], "from_failed_node": True},
        )

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["status"] == "skipped"
    assert result["reason_code"] == "not_failed"


def test_batch_rerun_requires_node_key_or_from_failed(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={"job_ids": ["any"]},
        )

    assert response.status_code == 422


def test_rerun_node_errors(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
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

        # Job not found
        resp = c.post("/api/jobs/nonexistent/nodes/fetch_questions/rerun")
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
        app.state.job_db.start_node_run(
            job_id,
            "fetch_questions",
            ["cmd"],
            f"logs/jobs/{job_id}-fetch_questions.log",
        )
        resp = c.post(f"/api/jobs/{job_id}/nodes/fetch_questions/rerun")
    assert resp.status_code == 400
    assert "running" in resp.json()["detail"].lower()


def test_rerun_node_cleanup_failed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
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

        def _fail_cleanup(*args, **kwargs):
            raise ValueError("cannot remove artifact")

        monkeypatch.setattr(
            "server.app.services.job_artifact_mutation.JobArtifactMutationService.stage_outputs",
            _fail_cleanup,
        )
        resp = c.post(f"/api/jobs/{job_id}/nodes/fetch_questions/rerun")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cannot remove artifact"


def test_rerun_node_mark_for_rerun_value_error(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
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

        def _fail_mark(*args, **kwargs):
            raise ValueError("invalid node state")

        monkeypatch.setattr(
            "server.app.jobs.atomic_mutations.AtomicJobMutationsMixin."
            "mark_nodes_for_rerun_in_transaction",
            _fail_mark,
        )

        resp = c.post(f"/api/jobs/{job_id}/nodes/fetch_questions/rerun")
    assert resp.status_code == 400
    assert "invalid node state" in resp.json()["detail"].lower()


def test_rerun_node_preserves_ancestors(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q700"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        app.state.job_db.update_job_node(job_id, "fetch_questions", status="completed")
        c.post(f"/api/jobs/{job_id}/nodes/review_key_info/rerun")
        detail = c.get(f"/api/jobs/{job_id}").json()

    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["fetch_questions"] == "completed"
    assert nodes["review_key_info"] == "pending"


def test_batch_rerun_node_not_found_for_one_job(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post(
            "/api/workspaces",
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q701"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_comprehension_info_Q701"
        # Remove downstream nodes so the selected node is absent from this job.
        job_db = app.state.job_db
        with job_db.connect() as conn:
            conn.execute(
                "delete from job_nodes where job_id=? and node_key=?",
                (job_id, "review_key_info"),
            )
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={"job_ids": [job_id], "node_key": "review_key_info"},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["reason_code"] == "node_not_found"


def test_batch_rerun_mixed_node_availability(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.cms.question import CmsQuestionDetail
    from server.app.main import create_app

    def fake_fetch_question_detail(question_id, api_url=None, token=None):
        return CmsQuestionDetail(
            question_id=question_id,
            title=f"Reading {question_id}",
            normalized={},
            payload={"uuid": question_id},
        )

    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.services.job_intake_resolution.get_token", lambda env, config: "token"
    )

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post(
            "/api/workspaces",
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q702A"],
            },
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q702B"],
            },
        )
        q_job_id = "test_question_comprehension_info_Q702A"
        r_job_id = "test_question_comprehension_info_Q702B"
        # Remove the target node from the second job so it fails with node_not_found.
        job_db = app.state.job_db
        with job_db.connect() as conn:
            conn.execute(
                "delete from job_nodes where job_id=? and node_key=?",
                (r_job_id, "review_key_info"),
            )
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={"job_ids": [q_job_id, r_job_id], "node_key": "review_key_info"},
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
        c.post(
            "/api/workspaces",
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q703", "Q704"],
                "knowledge_codes": [],
            },
        )
        first = "test_question_comprehension_info_Q703"
        second = "test_question_comprehension_info_Q704"
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={
                "job_ids": [second, first],
                "node_key": "review_key_info",
            },
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["job_id"] for r in results] == [second, first]
    assert all(r["status"] == "succeeded" for r in results)


def test_rerun_node_rejects_active_lease(tmp_path):
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    from server.app.executors._lease_transactions import _database_timestamp
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post(
            "/api/workspaces",
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q705"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_comprehension_info_Q705"
        job_db = app.state.job_db
        run = job_db.start_node_run(job_id, "review_key_info", ["cmd"], "logs/jobs/run.log")
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
                    "question_comprehension_info",
                    "review_key_info",
                    run["id"],
                    _database_timestamp(now),
                    _database_timestamp(now),
                    _database_timestamp(now + timedelta(seconds=300)),
                ),
            )
        resp = c.post(f"/api/jobs/{job_id}/nodes/review_key_info/rerun")

    assert resp.status_code == 400
    assert "active" in resp.json()["detail"].lower()


def test_rerun_node_expired_lease_not_blocking(tmp_path):
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    from server.app.executors._lease_transactions import _database_timestamp
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post(
            "/api/workspaces",
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q706"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_comprehension_info_Q706"
        job_db = app.state.job_db
        run = job_db.start_node_run(job_id, "review_key_info", ["cmd"], "logs/jobs/run.log")
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
                    "question_comprehension_info",
                    "review_key_info",
                    run["id"],
                    _database_timestamp(now),
                    _database_timestamp(now),
                    _database_timestamp(now - timedelta(seconds=1)),
                ),
            )
        resp = c.post(f"/api/jobs/{job_id}/nodes/review_key_info/rerun")
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["review_key_info"] == "pending"


def test_rerun_node_rollback_on_db_failure(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        c.post(
            "/api/workspaces",
            json={"name": "Test", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q707"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_comprehension_info_Q707"
        storage = resolve_job_dir(app.state.job_db.get_job(job_id), app.state.settings.jobs_dir)
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "review_key_info.json").write_text("understanding")

        def _fail(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "server.app.jobs.atomic_mutations.AtomicJobMutationsMixin."
            "mark_nodes_for_rerun_in_transaction",
            _fail,
        )
        resp = c.post(f"/api/jobs/{job_id}/nodes/review_key_info/rerun")

    assert resp.status_code == 400
    assert (storage / "review_key_info.json").read_text() == "understanding"
