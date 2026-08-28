from server.app.storage_paths import resolve_job_dir
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


def test_rerun_node_marks_downstream_stale(tmp_path):
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
                "knowledge_point_ids": ["Q201"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(f"/api/jobs/{job_id}/nodes/write_script/rerun")
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["node_key"] == "write_script"
    assert body["operation"] == "rerun"
    assert body["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["write_script"] == "pending"
    assert nodes["intake_knowledge_points"] == "pending"
    assert nodes["review_script"] == "stale"
    assert nodes["publish_content"] == "stale"
    assert nodes["generate_questions"] == "pending"
    assert nodes["review_questions"] == "pending"


def test_workspace_batch_rerun_marks_jobs_queued(tmp_path):
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
                "knowledge_point_ids": ["Q603"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        app.state.job_db.update_job_status(job_id, "failed", "boom")
        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun",
            json={"job_ids": [job_id], "node_key": "intake_knowledge_points"},
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "job_id": job_id,
            "operation": "rerun",
            "status": "succeeded",
            "node_key": "intake_knowledge_points",
            "reason_code": None,
            "message": None,
        }
    ]
    assert detail["job"]["status"] == "queued"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["intake_knowledge_points"] == "pending"
    assert nodes["publish_content"] == "stale"


def test_batch_rerun_skips_not_found_and_running_jobs(tmp_path):
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
        # Rerun non-existent job
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={"job_ids": ["nonexistent"], "node_key": "intake_knowledge_points"},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(r["status"] == "failed" and r["reason_code"] == "not_found" for r in results)


def test_batch_rerun_from_failed_node(tmp_path):
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
                "knowledge_point_ids": ["Q701"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        app.state.job_db.update_job_status(job_id, "failed", "boom")
        app.state.job_db.update_job_node(job_id, "write_script", status="failed")

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
            "node_key": "write_script",
            "reason_code": None,
            "message": None,
        }
    ]
    assert detail["job"]["status"] == "queued"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["write_script"] == "pending"


def test_batch_rerun_from_failed_node_skips_non_failed(tmp_path):
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
                "knowledge_point_ids": ["Q702"],
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
    with authenticate_client(TestClient(app)) as c:
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

        # Job not found
        resp = c.post("/api/jobs/nonexistent/nodes/intake_knowledge_points/rerun")
        assert resp.status_code == 404

        # Node not found
        resp = c.post(f"/api/jobs/{job_id}/nodes/nonexistent/rerun")
        assert resp.status_code == 404


def test_rerun_node_rejects_running_job(tmp_path):

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
        log_path = log_dir / f"{job_id}-fetch_items.log"
        log_path.write_text("running")
        app.state.job_db.start_node_run(
            job_id,
            "intake_knowledge_points",
            ["cmd"],
            f"logs/jobs/{job_id}-fetch_items.log",
        )
        resp = c.post(f"/api/jobs/{job_id}/nodes/intake_knowledge_points/rerun")
    assert resp.status_code == 400
    assert "running" in resp.json()["detail"].lower()


def test_rerun_node_cleanup_failed(tmp_path, monkeypatch):
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

        def _fail_cleanup(*args, **kwargs):
            raise ValueError("cannot remove artifact")

        monkeypatch.setattr(
            "server.app.services.job_artifact_mutation.JobArtifactMutationService.stage_outputs",
            _fail_cleanup,
        )
        resp = c.post(f"/api/jobs/{job_id}/nodes/intake_knowledge_points/rerun")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cannot remove artifact"


def test_rerun_node_mark_for_rerun_value_error(tmp_path, monkeypatch):
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

        def _fail_mark(*args, **kwargs):
            raise ValueError("invalid node state")

        monkeypatch.setattr(
            "server.app.jobs.atomic_mutations.AtomicJobMutationsMixin."
            "mark_nodes_for_rerun_in_transaction",
            _fail_mark,
        )

        resp = c.post(f"/api/jobs/{job_id}/nodes/intake_knowledge_points/rerun")
    assert resp.status_code == 400
    assert "invalid node state" in resp.json()["detail"].lower()


def test_rerun_node_preserves_ancestors(tmp_path):
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
                "knowledge_point_ids": ["Q700"],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        app.state.job_db.update_job_node(job_id, "intake_knowledge_points", status="completed")
        c.post(f"/api/jobs/{job_id}/nodes/publish_content/rerun")
        detail = c.get(f"/api/jobs/{job_id}").json()

    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["intake_knowledge_points"] == "completed"
    assert nodes["publish_content"] == "pending"


def test_batch_rerun_node_not_found_for_one_job(tmp_path):
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
                "knowledge_point_ids": ["Q701"],
            },
        )
        job_id = "test_test_Q701"
        # Remove downstream nodes so the selected node is absent from this job.
        job_db = app.state.job_db
        with job_db.connect() as conn:
            conn.execute(
                "delete from job_nodes where job_id=%s and node_key=%s",
                (job_id, "publish_content"),
            )
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={"job_ids": [job_id], "node_key": "publish_content"},
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["reason_code"] == "node_not_found"


def test_batch_rerun_mixed_node_availability(tmp_path):
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
                "knowledge_point_ids": ["Q702A"],
            },
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "workflow_key": "test",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q702B"],
            },
        )
        q_job_id = "test_test_Q702A"
        r_job_id = "test_test_Q702B"
        # Remove the target node from the second job so it fails with node_not_found.
        job_db = app.state.job_db
        with job_db.connect() as conn:
            conn.execute(
                "delete from job_nodes where job_id=%s and node_key=%s",
                (r_job_id, "publish_content"),
            )
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={"job_ids": [q_job_id, r_job_id], "node_key": "publish_content"},
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
                "knowledge_point_ids": ["Q703", "Q704"],
            },
        )
        first = "test_test_Q703"
        second = "test_test_Q704"
        resp = c.post(
            "/api/workspaces/test/jobs/batch-rerun",
            json={
                "job_ids": [second, first],
                "node_key": "publish_content",
            },
        )

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert [r["job_id"] for r in results] == [second, first]
    assert all(r["status"] == "succeeded" for r in results)


def test_rerun_node_rejects_active_lease(tmp_path):
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    from server.app.executors._lease_transactions import database_timestamp
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
                "knowledge_point_ids": ["Q705"],
            },
        )
        job_id = "test_test_Q705"
        job_db = app.state.job_db
        run = job_db.start_node_run(job_id, "publish_content", ["cmd"], "logs/jobs/run.log")
        now = datetime.now(UTC)
        with job_db.connect() as conn:
            conn.execute(
                """
                insert into executor_leases(
                    id, execution_id, executor_id, workspace_id, job_id, workflow_key,
                    node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
                """,
                (
                    "lease-1",
                    "exec-1",
                    "code-default",
                    "test",
                    job_id,
                    "education_video_problems_generation",
                    "publish_content",
                    run["id"],
                    database_timestamp(now),
                    database_timestamp(now),
                    database_timestamp(now + timedelta(seconds=300)),
                ),
            )
        resp = c.post(f"/api/jobs/{job_id}/nodes/publish_content/rerun")

    assert resp.status_code == 400
    assert "active" in resp.json()["detail"].lower()


def test_rerun_node_expired_lease_not_blocking(tmp_path):
    from datetime import UTC, datetime, timedelta

    from fastapi.testclient import TestClient

    from server.app.executors._lease_transactions import database_timestamp
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
                "knowledge_point_ids": ["Q706"],
            },
        )
        job_id = "test_test_Q706"
        job_db = app.state.job_db
        run = job_db.start_node_run(job_id, "publish_content", ["cmd"], "logs/jobs/run.log")
        job_db.finish_node_run(run["id"], "failed", 1, "expired")
        now = datetime.now(UTC)
        with job_db.connect() as conn:
            conn.execute(
                """
                insert into executor_leases(
                    id, execution_id, executor_id, workspace_id, job_id, workflow_key,
                    node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
                """,
                (
                    "lease-1",
                    "exec-1",
                    "code-default",
                    "test",
                    job_id,
                    "education_video_problems_generation",
                    "publish_content",
                    run["id"],
                    database_timestamp(now),
                    database_timestamp(now),
                    database_timestamp(now - timedelta(seconds=1)),
                ),
            )
        resp = c.post(f"/api/jobs/{job_id}/nodes/publish_content/rerun")
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["publish_content"] == "pending"


def test_rerun_node_rollback_on_db_failure(tmp_path, monkeypatch):
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
                "knowledge_point_ids": ["Q707"],
            },
        )
        job_id = "test_test_Q707"
        storage = resolve_job_dir(app.state.job_db.get_job(job_id), app.state.settings.jobs_dir)
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "publish_payload.json").write_text("understanding")

        def _fail(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "server.app.jobs.atomic_mutations.AtomicJobMutationsMixin."
            "mark_nodes_for_rerun_in_transaction",
            _fail,
        )
        resp = c.post(f"/api/jobs/{job_id}/nodes/publish_content/rerun")

    assert resp.status_code == 400
    assert (storage / "publish_payload.json").read_text() == "understanding"


def _create_failed_job(client, app, ws_id: str, question_id: str) -> str:
    created = client.post(
        f"/api/workspaces/{ws_id}/job-batches",
        json={
            "workflow_key": "test",
            "source_kind": "direct_ids",
            "knowledge_point_ids": [question_id],
        },
    ).json()
    job_id = created["jobs"][0]["id"]
    app.state.job_db.update_job_status(job_id, "failed", "boom")
    return job_id


def _fail_node_run(app, job_id: str, node_key: str, category: str, detail: str) -> None:
    job_db = app.state.job_db
    run = job_db.start_node_run(job_id, node_key, ["cmd"], f"logs/jobs/{job_id}-{node_key}.log")
    assert run is not None
    with job_db.connect() as conn:
        conn.execute(
            "update node_runs set status='failed', error_message='boom',"
            " failure_category=%s, failure_detail=%s, finished_at=current_timestamp"
            " where id=%s",
            (category, detail, run["id"]),
        )
        conn.execute(
            "update job_nodes set status='failed', error_message='boom'"
            " where job_id=%s and node_key=%s",
            (job_id, node_key),
        )
        conn.execute("update jobs set status='failed' where id=%s", (job_id,))
        conn.execute("commit")


def test_batch_rerun_preview_node_mode_counts_eligible(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        job_a = _create_failed_job(c, app, ws_id, "Q801")
        job_b = _create_failed_job(c, app, ws_id, "Q802")

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
            json={"filter": {"status": "failed"}, "node_key": "intake_knowledge_points"},
        )
        missing = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
            json={"filter": {"status": "failed"}, "node_key": "no_such_node"},
        )
        after = c.get(f"/api/jobs/{job_a}").json()

    assert response.status_code == 200
    assert response.json() == {"total_count": 2, "eligible_count": 2}
    assert missing.json() == {"total_count": 2, "eligible_count": 0}
    # 只读端点：job 状态不被改动。
    assert after["job"]["status"] == "failed"
    assert job_b != job_a


def test_batch_rerun_preview_from_failed_node_skips_non_failed(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        failed_job = _create_failed_job(c, app, ws_id, "Q803")
        app.state.job_db.update_job_node(failed_job, "write_script", status="failed")
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "test",
                "source_kind": "direct_ids",
                "knowledge_point_ids": ["Q804"],
            },
        ).json()
        pending_job = created["jobs"][0]["id"]

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
            json={"job_ids": [failed_job, pending_job], "from_failed_node": True},
        )

    assert response.status_code == 200
    assert response.json() == {"total_count": 2, "eligible_count": 1}


def test_batch_rerun_preview_failure_category_counts_matching(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        technical_job = _create_failed_job(c, app, ws_id, "Q805")
        _fail_node_run(app, technical_job, "review_script", "technical", "model_error")
        business_job = _create_failed_job(c, app, ws_id, "Q806")
        _fail_node_run(app, business_job, "write_script", "business", "empty_content")

        response = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
            json={"filter": {"status": "failed"}, "failure_category": "technical"},
        )

    assert response.status_code == 200
    assert response.json() == {"total_count": 2, "eligible_count": 1}


def test_batch_rerun_preview_validates_mode(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with authenticate_client(TestClient(app)) as c:
        ws_id = _create_workspace(c)
        no_mode = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
            json={"job_ids": ["any"]},
        )
        combined = c.post(
            f"/api/workspaces/{ws_id}/jobs/batch-rerun/preview",
            json={
                "job_ids": ["any"],
                "node_key": "intake_knowledge_points",
                "failure_category": "technical",
            },
        )

    assert no_mode.status_code == 422
    assert combined.status_code == 422
