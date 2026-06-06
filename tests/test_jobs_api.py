import json
from pathlib import Path


def test_job_routes_are_hidden_when_pipelines_disabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = False
    with TestClient(app) as c:
        response = c.get("/api/jobs")
        workspaces = c.get("/api/workspaces")

    assert response.status_code == 404
    assert workspaces.status_code == 404


def test_create_question_jobs_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "question_ids",
                "question_ids": ["Q001", "Q002"],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert body["jobs"][0]["workspace_id"] == "default"
    assert [job["source_id"] for job in body["jobs"]] == ["Q001", "Q002"]


def test_create_workspace_and_scoped_jobs_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        workspace_response = c.post("/api/workspaces", json={"name": "Math Sprint"})
        workspace_id = workspace_response.json()["workspace"]["id"]
        created = c.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "question_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
            },
        )
        workspace_jobs = c.get(f"/api/workspaces/{workspace_id}/jobs?pipeline_key=question_content")
        default_jobs = c.get("/api/jobs?pipeline_key=question_content")

    assert workspace_response.status_code == 200
    assert workspace_id == "math_sprint"
    assert created.status_code == 200
    body = created.json()
    assert body["jobs"][0]["workspace_id"] == workspace_id
    assert body["jobs"][0]["id"] == f"{workspace_id}_question_content_Q001"
    assert [job["id"] for job in workspace_jobs.json()["jobs"]] == [body["jobs"][0]["id"]]
    assert default_jobs.json()["jobs"] == []


def test_workspace_job_batch_stores_normalized_source_payload(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "question_ids",
                "question_ids": ["Q001", " Q002 ", "Q001", ""],
                "knowledge_codes": ["K001"],
            },
        )

    assert response.status_code == 200
    body = response.json()
    payload = json.loads(body["batch"]["source_payload_json"])
    assert payload["question_ids"] == ["Q001", "Q002"]
    assert payload["knowledge_codes"] == []
    assert body["created_count"] == 2


def test_get_job_detail_and_artifact_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "question_ids",
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


def test_get_pipeline_definition_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.get("/api/pipelines/question_content")

    assert response.status_code == 200
    body = response.json()
    assert body["pipeline"]["key"] == "question_content"
    assert body["pipeline"]["label"] == "题目内容生成"
    assert body["pipeline"]["concurrency"] == {"local": 8, "agent": 2}
    node_keys = [node["key"] for node in body["pipeline"]["nodes"]]
    assert node_keys[0] == "fetch_question_context"
    assert "assemble_package" in node_keys
    graph_node = next(
        node for node in body["pipeline"]["nodes"] if node["key"] == "content_graph_generation"
    )
    assert graph_node["runner"] == "agent"
    assert graph_node["after"] == ["solution_decomposition"]


def test_create_workspace_job_batch_rejects_empty_question_ids(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "question_ids",
                "question_ids": [" ", ""],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one question_id is required"


def test_rerun_node_marks_downstream_stale(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "question_ids",
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
    assert set(body["stale_nodes"]) == {
        "misconception_analysis",
        "natural_language_reading",
        "solution_decomposition",
        "faq_generation",
        "content_graph_generation",
        "interactive_template_generation",
        "content_review",
        "assemble_package",
    }
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["question_understanding"] == "pending"
    assert nodes["misconception_analysis"] == "stale"
    assert nodes["assemble_package"] == "stale"
