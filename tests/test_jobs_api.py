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
                "source_kind": "direct_ids",
                "question_ids": ["Q001", "Q002"],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert body["jobs"][0]["workspace_id"] == "default"
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
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
                "source_kind": "direct_ids",
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
    assert body["jobs"][0]["source_type"] == "question"
    assert [job["id"] for job in workspace_jobs.json()["jobs"]] == [body["jobs"][0]["id"]]
    assert default_jobs.json()["jobs"] == []


def test_workspace_settings_round_trip(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        connection = c.patch(
            "/api/workspaces/default/settings/connection",
            json={"resources": {"question_detail": {"enabled": True, "config": {}}}},
        )
        intake = c.patch(
            "/api/workspaces/default/settings/intake",
            json={
                "entityType": "video",
                "intakeModes": ["direct_ids"],
                "labelOverrides": {"direct_ids": "输入 ID"},
            },
        )
        pipeline = c.patch(
            "/api/workspaces/default/settings/pipeline",
            json={"pipelineKey": "question_content"},
        )
        fetched = c.get("/api/workspaces/default/settings")
        test_connection = c.post("/api/workspaces/default/settings/test-connection")

    assert connection.status_code == 200
    assert intake.status_code == 200
    assert pipeline.status_code == 200
    assert test_connection.status_code == 200
    settings = fetched.json()["settings"]
    assert "cmsUrl" not in settings
    assert "cmsToken" not in settings
    assert settings["resources"]["question_detail"]["enabled"] is True
    assert settings["entityType"] == "video"
    assert settings["intakeModes"] == ["direct_ids"]
    assert settings["labelOverrides"] == {"direct_ids": "输入 ID"}
    assert settings["pipelineKey"] == "question_content"


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
                "source_kind": "direct_ids",
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


def test_create_workspace_job_batch_from_knowledge_codes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.cms.question import CmsQuestionSummary
    from server.app.main import create_app

    calls = []

    def fake_list_questions_by_knowledge(code, api_url=None, token=None):
        calls.append({"code": code, "api_url": api_url, "token": token})
        return [
            CmsQuestionSummary("Q001", "题目一", {"uuid": "Q001"}),
            CmsQuestionSummary("Q002", "题目二", {"uuid": "Q002"}),
        ]

    monkeypatch.setattr(
        "server.app.routes.jobs.list_questions_by_knowledge",
        fake_list_questions_by_knowledge,
    )
    monkeypatch.setattr("server.app.routes.jobs.get_token", lambda env, config: "token")

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "question_list_url": "https://cms.example/question/list?bank_version=v5&page_size=50",
    }
    with TestClient(app) as c:
        response = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K001", "K001", " K002 "],
            },
        )

    assert response.status_code == 200
    body = response.json()
    payload = json.loads(body["batch"]["source_payload_json"])
    assert [call["code"] for call in calls] == ["K001", "K002"]
    assert calls[0]["api_url"] == "https://cms.example/question/list?bank_version=v5&page_size=50"
    assert calls[0]["token"] == "token"
    assert payload["knowledge_codes"] == ["K001", "K002"]
    assert payload["question_ids"] == ["Q001", "Q002"]
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["title"] for job in body["jobs"]] == ["题目一", "题目二"]


def test_create_workspace_job_batch_from_resource_binding(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.cms.question import CmsQuestionSummary
    from server.app.main import create_app

    calls = []

    def fake_list_questions_by_knowledge(code, api_url=None, token=None):
        calls.append({"code": code, "api_url": api_url, "token": token})
        return [CmsQuestionSummary("Q101", "资源绑定题目", {"uuid": "Q101"})]

    monkeypatch.setattr(
        "server.app.routes.jobs.list_questions_by_knowledge",
        fake_list_questions_by_knowledge,
    )
    monkeypatch.setattr("server.app.routes.jobs.get_token", lambda env, config: "token")

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["cms"] = {"env": "prod"}
    app.state.settings.config["resource_providers"] = {
        "cms.question.list_by_knowledge": {
            "api_url": "https://cms.example/question/list",
        }
    }
    with TestClient(app) as c:
        workspace = c.post(
            "/api/workspaces",
            json={
                "name": "Resource Math",
                "resource_config": {
                    "resources": {
                        "by_knowledge": {
                            "provider": "cms.question.list_by_knowledge",
                            "config": {
                                "bank_version": "v5",
                                "subject_id": "5",
                            },
                        }
                    }
                },
            },
        ).json()["workspace"]
        response = c.post(
            f"/api/workspaces/{workspace['id']}/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K101"],
            },
        )

    assert response.status_code == 200
    assert calls == [
        {
            "code": "K101",
            "api_url": "https://cms.example/question/list?bank_version=v5&subject_id=5",
            "token": "token",
        }
    ]
    payload = json.loads(response.json()["batch"]["source_payload_json"])
    assert payload["resource_config"]["resources"]["by_knowledge"]["provider"] == (
        "cms.question.list_by_knowledge"
    )
    assert response.json()["jobs"][0]["source_type"] == "question"


def test_create_workspace_stores_cms_config_override(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.post(
            "/api/workspaces",
            json={
                "name": "Math V5",
                "cms_config": {
                    "subject_id": "5",
                    "question_detail_url": "https://cms.example/question/detail?bank_version=v5",
                },
            },
        )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["cms_config"]["subject_id"] == "5"
    assert (
        workspace["cms_config"]["question_detail_url"]
        == "https://cms.example/question/detail?bank_version=v5"
    )


def test_update_workspace_cms_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        created = c.post("/api/workspaces", json={"name": "Math V5"}).json()
        workspace_id = created["workspace"]["id"]
        response = c.patch(
            f"/api/workspaces/{workspace_id}",
            json={
                "cms_config": {
                    "question_list_url": "https://cms.example/question/list?bank_version=v5",
                    "question_detail_url": "https://cms.example/question/detail?bank_version=v5",
                    "subject_id": "5",
                    "country_id": "1",
                }
            },
        )
        fetched = c.get(f"/api/workspaces/{workspace_id}")

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert workspace["cms_config"]["subject_id"] == "5"
    assert (
        workspace["cms_config"]["question_list_url"]
        == "https://cms.example/question/list?bank_version=v5"
    )
    assert fetched.json()["workspace"]["cms_config"] == workspace["cms_config"]


def test_update_workspace_resource_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        created = c.post("/api/workspaces", json={"name": "Math Resources"}).json()
        workspace_id = created["workspace"]["id"]
        response = c.patch(
            f"/api/workspaces/{workspace_id}",
            json={
                "resource_config": {
                    "resources": {
                        "question_detail": {
                            "provider": "cms.question.detail",
                            "config": {
                                "api_url": "https://cms.example/question/detail",
                                "subject_id": "5",
                            },
                        },
                        "by_knowledge": {
                            "provider": "cms.question.list_by_knowledge",
                            "config": {
                                "api_url": "https://cms.example/question/list",
                                "page_size": 50,
                            },
                        },
                    }
                }
            },
        )

    assert response.status_code == 200
    resources = response.json()["workspace"]["resource_config"]["resources"]
    assert resources["question_detail"]["provider"] == "cms.question.detail"
    assert resources["question_detail"]["config"]["subject_id"] == "5"
    assert resources["by_knowledge"]["config"]["page_size"] == 50


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


def test_job_detail_includes_pi_run_trace(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "reading_analysis",
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
            "events.jsonl",
            run_dir=str(tmp_path / "run-1"),
            session_dir=str(tmp_path / "run-1" / "session"),
        )
        app.state.job_db.finish_node_run(run["id"], "completed", 0, "")

        detail = c.get(f"/api/jobs/{job_id}")

    assert detail.status_code == 200
    body = detail.json()
    runs = body["runs"]
    assert len(runs) == 1
    assert "/runs/extract_keywords/" in runs[0]["run_dir"]
    assert runs[0]["session_dir"] == f"{runs[0]['run_dir']}/session"
    assert json.loads(runs[0]["command_json"])[0] == "pi"


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
    assert body["pipeline"]["intake"]["modes"] == [
        {
            "key": "direct_ids",
            "label": "直接输入 ID",
            "input_field": "question_ids",
            "resource": "",
        },
        {
            "key": "by_knowledge",
            "label": "按知识点查询",
            "input_field": "knowledge_codes",
            "resource": "by_knowledge",
        },
    ]
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
                "source_kind": "direct_ids",
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


def test_job_detail_includes_node_dependencies(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q202"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    nodes = {node["node_key"]: node for node in response.json()["nodes"]}
    assert nodes["content_graph_generation"]["after"] == ["solution_decomposition"]


def test_workspace_stats_hidden_when_pipelines_disabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = False
    with TestClient(app) as c:
        response = c.get("/api/workspaces/default/stats")
    assert response.status_code == 404


def test_workspace_stats_returns_counts_and_agent_status(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        ws = c.post("/api/workspaces", json={"name": "Stats WS"}).json()
        ws_id = ws["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q301", "Q302"],
                "knowledge_codes": [],
            },
        )
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    assert body["workspace_id"] == ws_id
    assert body["name"] == "Stats WS"
    assert body["pipeline_key"] == "question_content"
    assert body["pipeline_label"] == "题目内容生成"
    assert body["job_stats"]["queued"] == 2
    assert body["agent_status"]["total"] == 0
    assert body["agent_status"]["busy"] == 0
    assert body["agent_status"]["idle"] == 0
    assert body["latest_run"] is None


def test_workspace_stats_filters_agents_by_assignment(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.agents import AgentStatus
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True

    manager = app.state.agent_manager
    manager.agents = [
        AgentStatus(id="agent-1", name="Agent One", busy=False, max_tasks=1),
        AgentStatus(id="agent-2", name="Agent Two", busy=True, max_tasks=1),
        AgentStatus(id="agent-3", name="Agent Three", busy=False, max_tasks=1),
    ]
    with TestClient(app) as c:
        ws = c.post("/api/workspaces", json={"name": "Stats WS"}).json()
        ws_id = ws["workspace"]["id"]
        manager.set_workspace_assignment(ws_id, "agent-1", 1)
        manager.set_workspace_assignment(ws_id, "agent-2", 1)
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    assert body["agent_status"]["total"] == 2
    assert body["agent_status"]["busy"] == 1
    assert body["agent_status"]["idle"] == 1
    assert len(body["agent_status"]["agents"]) == 2
    agent_ids = {a["id"] for a in body["agent_status"]["agents"]}
    assert agent_ids == {"agent-1", "agent-2"}
    agent_1 = next(a for a in body["agent_status"]["agents"] if a["id"] == "agent-1")
    assert agent_1["name"] == "Agent One"
    assert agent_1["busy"] is False
    agent_2 = next(a for a in body["agent_status"]["agents"] if a["id"] == "agent-2")
    assert agent_2["busy"] is True


def test_workspace_stats_latest_run_reflects_node_runs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q401"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        job_db = app.state.job_db
        run = job_db.start_node_run(job_id, "question_understanding", ["echo", "hi"], "/dev/null")
        job_db.finish_node_run(run["id"], "completed", 0, "")
        stats = c.get("/api/workspaces/default/stats")

    assert stats.status_code == 200
    body = stats.json()
    assert body["latest_run"] is not None
    assert body["latest_run"]["job_id"] == job_id
    assert body["latest_run"]["node_key"] == "question_understanding"
    assert body["latest_run"]["status"] == "completed"


def test_delete_workspace_hidden_when_pipelines_disabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = False
    with TestClient(app) as c:
        response = c.delete("/api/workspaces/some_ws")
    assert response.status_code == 404


def test_delete_workspace_rejects_default(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.delete("/api/workspaces/default")
    assert response.status_code == 400
    assert "default" in response.json()["detail"].lower()


def test_delete_workspace_rejects_when_jobs_running(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        ws = c.post("/api/workspaces", json={"name": "Running WS"}).json()
        ws_id = ws["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "pipeline_key": "question_content",
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


def test_delete_job_returns_404_for_unknown_job(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        resp = c.delete("/api/jobs/nonexistent")
    assert resp.status_code == 404


def test_delete_job_rejects_running_job(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q601"],
                "knowledge_codes": [],
            },
        )
        job_id = "default_question_content_Q601"
        job = app.state.job_db.get_job(job_id)
        storage_dir = Path(str(job["storage_dir"]))
        storage_dir.mkdir(parents=True, exist_ok=True)
        (storage_dir / "artifact.json").write_text("{}")
        log_dir = app.state.settings.logs_dir / "jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}-fetch_question_context.log"
        log_path.write_text("running")
        app.state.job_db.update_job_status(job_id, "running")
        resp = c.delete(f"/api/jobs/{job_id}")
    assert resp.status_code == 400
    assert "running" in resp.json()["detail"].lower()
    assert storage_dir.exists()
    assert (storage_dir / "artifact.json").exists()
    assert log_path.exists()


def test_delete_job_cascades_and_returns_deleted_id(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q602"],
                "knowledge_codes": [],
            },
        )
        job_id = "default_question_content_Q602"
        job = app.state.job_db.get_job(job_id)
        storage_dir = Path(str(job["storage_dir"]))
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


def test_workspace_batch_rerun_marks_jobs_queued(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        created = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q603"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        app.state.job_db.update_job_status(job_id, "failed", "boom")
        response = c.post(
            "/api/workspaces/default/jobs/batch-rerun",
            json={"job_ids": [job_id]},
        )
        detail = c.get(f"/api/jobs/{job_id}").json()

    assert response.status_code == 200
    assert response.json()["results"] == [
        {
            "job_id": job_id,
            "status": "rerun",
            "node_key": "fetch_question_context",
        }
    ]
    assert detail["job"]["status"] == "queued"
    nodes = {node["node_key"]: node["status"] for node in detail["nodes"]}
    assert nodes["fetch_question_context"] == "pending"
    assert nodes["question_understanding"] == "stale"


def test_workspace_batch_delete_removes_jobs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        created = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q604"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.request(
            "DELETE",
            "/api/workspaces/default/jobs/batch",
            json={"job_ids": [job_id]},
        )
        detail = c.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["results"] == [{"job_id": job_id, "status": "deleted"}]
    assert detail.status_code == 404


def test_workspace_stats_returns_404_for_unknown_workspace(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        resp = c.get("/api/workspaces/nonexistent/stats")
    assert resp.status_code == 404


def test_delete_workspace_cascades_and_returns_deleted_id(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        ws = c.post("/api/workspaces", json={"name": "Delete Me"}).json()
        ws_id = ws["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "pipeline_key": "question_content",
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
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
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
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
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
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
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


def test_workspace_intake_config_rejects_disabled_mode(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        workspace_response = c.post(
            "/api/workspaces",
            json={
                "name": "intake-filtered",
                "default_pipeline_key": "question_content",
                "intake_config": {"enabled_modes": ["direct_ids"]},
            },
        )
        workspace_id = workspace_response.json()["workspace"]["id"]

        response = c.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K001"],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Intake mode is disabled for this workspace"


def test_workspace_default_entity_is_used_when_batch_omits_entity(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        workspace_response = c.post(
            "/api/workspaces",
            json={
                "name": "video-default",
                "default_pipeline_key": "question_content",
                "default_entity": "video",
                "intake_config": {"enabled_modes": ["direct_ids"]},
            },
        )
        workspace_id = workspace_response.json()["workspace"]["id"]

        response = c.post(
            f"/api/workspaces/{workspace_id}/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["v001"],
                "knowledge_codes": [],
            },
        )
        body = response.json()
        batch = app.state.job_db.get_batch(body["batch"]["id"])

    assert response.status_code == 200
    assert body["jobs"][0]["source_type"] == "video"
    payload = json.loads(batch["source_payload_json"])
    assert payload["entity"] == "video"


def test_batch_with_entity_question(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "entity": "question",
                "source_kind": "direct_ids",
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
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "entity": "knowledge",
                "source_kind": "direct_ids",
                "question_ids": ["K001"],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 400
    assert "Unsupported entity and intake mode combination" in response.json()["detail"]


def test_batch_video_resolver_not_implemented(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "entity": "video",
                "source_kind": "by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K001"],
            },
        )

    assert response.status_code == 501
    assert "video resolver not yet implemented" in response.json()["detail"]


def test_batch_with_entity_video_direct_ids(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "entity": "video",
                "source_kind": "direct_ids",
                "question_ids": ["v1", "v2"],
                "knowledge_codes": [],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["video", "video"]
    assert [job["source_id"] for job in body["jobs"]] == ["v1", "v2"]
    assert [job["title"] for job in body["jobs"]] == ["Video v1", "Video v2"]
    payload = json.loads(body["batch"]["source_payload_json"])
    assert payload["entity"] == "video"
    assert payload["question_ids"] == ["v1", "v2"]
    assert [c["entity_id"] for c in payload["task_candidates"]] == ["v1", "v2"]
    assert [c["entity_type"] for c in payload["task_candidates"]] == ["video", "video"]


def test_pipeline_response_no_task_entity(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.get("/api/pipelines/question_content")

    assert response.status_code == 200
    body = response.json()
    for mode in body["pipeline"]["intake"]["modes"]:
        assert "task_entity" not in mode
        assert "resolver" not in mode
        assert "key" in mode
        assert "label" in mode
        assert "input_field" in mode
        assert "resource" in mode


def test_list_workspace_runs_returns_joined_job_metadata(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        batch = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
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
            "data/logs/jobs/run.log",
        )
        app.state.job_db.finish_node_run(run["id"], "completed", 0, "")

        response = c.get("/api/workspaces/default/runs")

    assert response.status_code == 200
    body = response.json()
    assert len(body["runs"]) == 1
    assert body["runs"][0]["workspace_id"] == "default"
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
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        batch = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = batch["jobs"][0]["id"]
        run1 = app.state.job_db.start_node_run(job_id, "fetch_question_context", ["local"], "a.log")
        app.state.job_db.finish_node_run(run1["id"], "completed", 0, "")
        run2 = app.state.job_db.start_node_run(job_id, "assemble_package", ["local"], "b.log")
        app.state.job_db.finish_node_run(run2["id"], "failed", 1, "boom")

        response = c.get("/api/workspaces/default/runs?status=failed&node_key=assemble_package")

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["node_key"] == "assemble_package"
    assert runs[0]["status"] == "failed"
    assert runs[0]["error_message"] == "boom"


def test_get_workspace_dag_returns_node_status_counts(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q001", "Q002"],
                "knowledge_codes": [],
            },
        )
        response = c.get("/api/workspaces/default/dag")

    assert response.status_code == 200
    body = response.json()
    assert body["pipeline"]["key"] == "question_content"
    first = body["nodes"][0]
    assert first["key"] == "fetch_question_context"
    assert first["runner"] == "local"
    assert first["status_counts"]["pending"] == 2


def test_get_resource_providers_returns_provider_list(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["resource_providers"] = {
        "cms.question.detail": {"path": "/question/detail"},
        "cms.question.list_by_knowledge": {"path": "/question/list"},
    }
    app.state.settings.config["cms"] = {
        "env": "prod",
        "bank_version": "v5",
        "country_id": "1",
        "subject_id": "2",
    }
    with TestClient(app) as c:
        response = c.get("/api/resource-providers")

    assert response.status_code == 200
    body = response.json()
    providers = {p["key"]: p for p in body["providers"]}
    assert "question_detail" in providers
    assert providers["question_detail"]["provider"] == "cms.question.detail"
    assert providers["question_detail"]["path"] == "/question/detail"
    assert providers["question_detail"]["defaultParams"] == {
        "bank_version": "v5",
        "country_id": "1",
        "subject_id": "2",
    }
    assert providers["question_detail"]["paramKeys"] == ["bank_version", "country_id", "subject_id"]
    assert "by_knowledge" in providers
    assert providers["by_knowledge"]["provider"] == "cms.question.list_by_knowledge"
    assert providers["by_knowledge"]["paramKeys"] == [
        "bank_version",
        "country_id",
        "subject_id",
        "page_size",
    ]


def test_get_global_services_returns_cms_status(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "base_url": "http://cms.example.com/v2",
        "token": "secret123",
    }
    with TestClient(app) as c:
        response = c.get("/api/global-services")

    assert response.status_code == 200
    body = response.json()
    assert body["cms"]["baseUrl"] == "http://cms.***.cn/v2"
    assert body["cms"]["tokenConfigured"] is True
    assert body["cms"]["env"] == "prod"
    assert body["cms"]["healthy"] is None


def test_get_global_services_unconfigured_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    for key in (
        "BASECMS_APP_ID",
        "BASECMS_NONCE",
        "BASECMS_SECRET",
        "BASECMS_TOKEN_URL",
        "BASECMS_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["cms"] = {"env": "dev"}
    with TestClient(app) as c:
        response = c.get("/api/global-services")

    assert response.status_code == 200
    body = response.json()
    assert body["cms"]["tokenConfigured"] is False


def test_get_global_services_token_gen_configured(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "base_url": "http://cms.example/v2",
        "token_gen": {
            "app_id": "app1",
            "nonce": "n1",
            "secret": "s1",
            "url": "http://token.example/generate",
        },
    }
    with TestClient(app) as c:
        response = c.get("/api/global-services")

    assert response.status_code == 200
    body = response.json()
    assert body["cms"]["tokenConfigured"] is True


def test_workspace_settings_without_cms_fields(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.get("/api/workspaces/default/settings")

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert "cmsUrl" not in settings
    assert "cmsToken" not in settings
    assert "resources" in settings


def test_workspace_settings_returns_resource_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        c.patch(
            "/api/workspaces/default",
            json={
                "resource_config": {
                    "resources": {
                        "question_detail": {
                            "enabled": True,
                            "config": {"bank_version": "v6"},
                        }
                    }
                }
            },
        )
        response = c.get("/api/workspaces/default/settings")

    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["resources"]["question_detail"]["enabled"] is True
    assert settings["resources"]["question_detail"]["config"]["bank_version"] == "v6"


def test_patch_settings_connection_saves_resource_config(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.patch(
            "/api/workspaces/default/settings/connection",
            json={"resources": {"question_detail": {"enabled": False, "config": {}}}},
        )
        fetched = c.get("/api/workspaces/default/settings")

    assert response.status_code == 200
    assert fetched.json()["settings"]["resources"]["question_detail"]["enabled"] is False


def test_test_connection_uses_global_cms_url(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["cms"] = {
        "question_detail_url": "http://cms.example/detail",
        "token": "global_token",
    }
    with TestClient(app) as c:
        response = c.post("/api/workspaces/default/settings/test-connection")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_test_connection_fails_when_global_url_missing(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["cms"] = {}
    with TestClient(app) as c:
        response = c.post("/api/workspaces/default/settings/test-connection")

    assert response.status_code == 400
    assert "Global CMS URL" in response.json()["detail"]


def test_job_batch_rejects_disabled_resource_provider(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["resource_providers"] = {
        "cms.question.list_by_knowledge": {"api_url": "http://cms.example/list"},
    }
    with TestClient(app) as c:
        workspace = c.post("/api/workspaces", json={"name": "Disabled Resource"}).json()[
            "workspace"
        ]
        c.patch(
            f"/api/workspaces/{workspace['id']}",
            json={
                "resource_config": {"resources": {"by_knowledge": {"enabled": False, "config": {}}}}
            },
        )
        response = c.post(
            f"/api/workspaces/{workspace['id']}/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K001"],
            },
        )

    assert response.status_code == 400
    assert "disabled" in response.json()["detail"].lower()
