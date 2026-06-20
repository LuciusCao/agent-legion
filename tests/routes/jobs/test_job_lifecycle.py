import json
from pathlib import Path

from server.app.storage_paths import resolve_job_dir


def _create_workspace(client, name="default"):
    return client.post("/api/workspaces", json={"name": name}).json()["workspace"]["id"]


def test_get_job_detail_and_artifact_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q003"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        artifact = Path(created["jobs"][0]["storage_dir"]) / "question_context.json"
        artifact.write_text('{"question_id":"Q003"}', encoding="utf-8")

        detail = c.get(f"/api/jobs/{job_id}")
        artifact_response = c.get(f"/api/jobs/{job_id}/artifacts/question_context.json")
        traversal = c.get(f"/api/jobs/{job_id}/artifacts/../video_hive.sqlite")

    assert detail.status_code == 200
    assert detail.json()["job"]["id"] == job_id
    assert artifact_response.status_code == 200
    assert artifact_response.json()["content"] == '{"question_id":"Q003"}'
    assert traversal.status_code == 400


def test_job_detail_includes_pi_run_trace(tmp_path, monkeypatch):
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
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "reading_analysis",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q100"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        run = app.state.job_db.start_node_run(
            job_id,
            "extract_keywords",
            ["pi", "--mode", "json"],
            "logs/jobs/extract_keywords-events.jsonl",
            run_dir=str(tmp_path / "run-1"),
            session_dir=str(tmp_path / "run-1" / "session"),
        )
        app.state.job_db.finish_node_run(run["id"], "completed", 0, "")

        detail = c.get(f"/api/jobs/{job_id}")

    assert detail.status_code == 200
    body = detail.json()
    runs = body["runs"]
    assert len(runs) == 1
    assert runs[0]["run_dir"] == str(tmp_path / "run-1")
    assert runs[0]["session_dir"] == str(tmp_path / "run-1" / "session")
    assert json.loads(runs[0]["command_json"])[0] == "pi"


def test_job_detail_includes_node_dependencies(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q202"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert all("label" in node for node in response.json()["nodes"])
    nodes = {node["node_key"]: node for node in response.json()["nodes"]}
    assert nodes["content_graph_generation"]["after"] == ["solution_decomposition"]


def test_job_detail_includes_executor_binding_and_kind(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    job_db = app.state.job_db
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        created = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q203"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        job_db.replace_workspace_executor_configuration(
            ws_id,
            allocations=[
                {"executor_id": "local-default", "concurrency_limit": 1},
                {"executor_id": "pi-default", "concurrency_limit": 1},
            ],
            bindings=[
                {
                    "workflow_key": "question_content",
                    "node_key": "question_understanding",
                    "executor_id": "pi-default",
                },
                {
                    "workflow_key": "question_content",
                    "node_key": "assemble_package",
                    "executor_id": "local-default",
                },
            ],
            node_limits=[],
        )
        response = c.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    nodes = {node["node_key"]: node for node in response.json()["nodes"]}
    assert nodes["question_understanding"]["executor_id"] == "pi-default"
    assert nodes["question_understanding"]["executor_kind"] == "pi"
    assert nodes["assemble_package"]["executor_id"] == "local-default"
    assert nodes["assemble_package"]["executor_kind"] == "local"


def test_delete_job_returns_404_for_unknown_job(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        resp = c.delete("/api/jobs/nonexistent")
    assert resp.status_code == 404


def test_delete_job_rejects_running_job(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q601"],
                "knowledge_codes": [],
            },
        )
        job_id = f"{ws_id}_question_content_Q601"
        job = app.state.job_db.get_job(job_id)
        storage_dir = resolve_job_dir(job, app.state.settings.jobs_dir)
        storage_dir.mkdir(parents=True, exist_ok=True)
        (storage_dir / "artifact.json").write_text("{}")
        log_dir = app.state.settings.logs_dir / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}-fetch_question_context.log"
        log_path.write_text("running")
        # Start a node run so _job_has_running_nodes returns True
        app.state.job_db.start_node_run(
            job_id,
            "fetch_question_context",
            ["cmd"],
            f"logs/jobs/{job_id}-fetch_question_context.log",
        )
        resp = c.delete(f"/api/jobs/{job_id}")
    assert resp.status_code == 400
    assert "running" in resp.json()["detail"].lower()
    assert storage_dir.exists()
    assert (storage_dir / "artifact.json").exists()
    assert log_path.exists()


def test_delete_job_cascades_and_returns_deleted_id(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q602"],
                "knowledge_codes": [],
            },
        )
        job_id = f"{ws_id}_question_content_Q602"
        job = app.state.job_db.get_job(job_id)
        storage_dir = resolve_job_dir(job, app.state.settings.jobs_dir)
        storage_dir.mkdir(parents=True, exist_ok=True)
        (storage_dir / "artifact.json").write_text("{}")
        log_dir = app.state.settings.logs_dir / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{job_id}-fetch_question_context.log").write_text("ok")
        resp = c.delete(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == job_id
    assert not storage_dir.exists()
    assert not (log_dir / f"{job_id}-fetch_question_context.log").exists()


def test_list_workspace_runs_returns_joined_job_metadata(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        batch = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = batch["jobs"][0]["id"]
        run = app.state.job_db.start_node_run(
            job_id,
            "fetch_question_context",
            ["local", "fetch_question_context"],
            "logs/jobs/run.log",
        )
        app.state.job_db.finish_node_run(run["id"], "completed", 0, "")

        response = c.get(f"/api/workspaces/{ws_id}/runs")

    assert response.status_code == 200
    body = response.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["workspace_id"] == ws_id
    assert body["runs"][0]["job_id"] == job_id
    assert body["runs"][0]["job_title"] == "Question Q001"
    assert body["runs"][0]["source_id"] == "Q001"
    assert body["runs"][0]["source_type"] == "question"
    assert body["runs"][0]["node_key"] == "fetch_question_context"
    assert body["runs"][0]["status"] == "completed"


def test_list_workspace_runs_filters_by_status_and_node(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        ws_id = _create_workspace(c)
        batch = c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = batch["jobs"][0]["id"]
        run1 = app.state.job_db.start_node_run(
            job_id, "fetch_question_context", ["local"], "logs/a.log"
        )
        app.state.job_db.finish_node_run(run1["id"], "completed", 0, "")
        run2 = app.state.job_db.start_node_run(job_id, "assemble_package", ["local"], "logs/b.log")
        app.state.job_db.finish_node_run(run2["id"], "failed", 1, "boom")

        response = c.get(f"/api/workspaces/{ws_id}/runs?status=failed&node_key=assemble_package")

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["node_key"] == "assemble_package"
    assert runs[0]["status"] == "failed"
    assert runs[0]["error_message"] == "boom"


def test_get_workspace_dag_returns_node_status_counts(tmp_path, monkeypatch):
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
        ws_id = c.post(
            "/api/workspaces",
            json={"name": "Reading DAG", "default_workflow_key": "reading_analysis"},
        ).json()["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "workflow_key": "reading_analysis",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001", "Q002"],
                "knowledge_codes": [],
            },
        )
        response = c.get(f"/api/workspaces/{ws_id}/dag")

    assert response.status_code == 200
    body = response.json()
    assert body["workflow"]["key"] == "reading_analysis"
    assert all("label" in node for node in body["nodes"])
    first = body["nodes"][0]
    assert first["key"] == "fetch_questions"
    assert first["status_counts"]["pending"] == 2


def test_get_artifact_returns_404(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        # Job not found
        resp = c.get("/api/jobs/nonexistent/artifacts/test.json")
    assert resp.status_code == 404


def test_get_job_run_log_returns_redacted_tail(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    app.state.settings.config["secret_token"] = "leaked-token"
    log_dir = app.state.settings.logs_dir / "jobs"
    log_dir.mkdir(parents=True, exist_ok=True)

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
        log_path = log_dir / f"{job_id}-fetch_question_context.log"
        log_path.write_text("start\nleaked-token\nend\n", encoding="utf-8")
        run = app.state.job_db.start_node_run(
            job_id,
            "fetch_question_context",
            ["cmd"],
            f"logs/jobs/{job_id}-fetch_question_context.log",
        )

        resp = c.get(f"/api/jobs/{job_id}/runs/{run['id']}/log")

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run["id"]
    assert "leaked-token" not in body["log"]
    assert "<redacted>" in body["log"]
    assert "end" in body["log"]
    assert "truncated" in body


def test_get_job_run_log_returns_404_for_missing_run(tmp_path):
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
        resp = c.get(f"/api/jobs/{job_id}/runs/999999/log")
    assert resp.status_code == 404


def test_get_job_run_log_rejects_escape(tmp_path):
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
        run = app.state.job_db.start_node_run(
            job_id, "fetch_question_context", ["cmd"], "../escape.log"
        )
        resp = c.get(f"/api/jobs/{job_id}/runs/{run['id']}/log")
    assert resp.status_code == 400
    assert "Invalid log path" in resp.json()["detail"]


def test_reject_invalid_job_subpath(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True
    with TestClient(app) as c:
        # Job not found
        resp = c.get("/api/jobs/nonexistent/invalid/path")
    assert resp.status_code == 404


def test_job_detail_includes_node_inputs_outputs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.workflows.enabled = True

    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "WS"})
        batch = c.post(
            "/api/workspaces/ws/job-batches",
            json={
                "workflow_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = batch["jobs"][0]["id"]
        response = c.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    for node in body["nodes"]:
        assert "inputs" in node
        assert "outputs" in node
        assert isinstance(node["inputs"], list)
        assert isinstance(node["outputs"], list)
