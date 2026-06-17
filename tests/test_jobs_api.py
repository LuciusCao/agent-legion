import json
from pathlib import Path


def test_delete_job_response_model_is_exposed_in_openapi(tmp_path):
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    schema = app.openapi()

    assert (
        schema["paths"]["/api/jobs/{job_id}"]["delete"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/DeleteJobResponse"
    )

    schemas = schema["components"]["schemas"]
    delete_schema = schemas["DeleteJobResponse"]
    assert set(delete_schema["required"]) == {"deleted"}
    assert delete_schema["properties"]["deleted"]["type"] == "string"


def test_workspace_agent_routes_are_absent_from_openapi(tmp_path):
    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    schema = app.openapi()

    assert "/api/workspaces/{workspace_id}/agents" not in schema["paths"]
    assert "WorkspaceAgentListResponse" not in schema["components"]["schemas"]
    assert "WorkspaceAgentAssignmentResponse" not in schema["components"]["schemas"]
    assert "WorkspaceAgentConfig" not in schema["components"]["schemas"]


def test_job_routes_are_hidden_when_pipelines_disabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = False
    with TestClient(app) as c:
        response = c.get("/api/jobs")
        workspaces = c.get("/api/workspaces")

    assert response.status_code == 404
    assert workspaces.status_code == 404


def test_create_question_jobs_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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


def test_workspace_configuration_saves_all_sections_atomically(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        response = c.put(
            "/api/workspaces/default/configuration",
            json={
                "name": "Updated Workspace",
                "description": "Atomic settings",
                "settings": {
                    "entityType": "video",
                    "intakeModes": ["direct_ids"],
                    "labelOverrides": {"direct_ids": "Video IDs"},
                    "pipelineKey": "question_content",
                    "resources": {"question_detail": {"enabled": True, "config": {}}},
                },
                "executor_allocations": [
                    {"executor_id": "local-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "pipeline_key": "question_content",
                        "node_key": "fetch_question_context",
                        "executor_id": "local-default",
                    },
                ],
                "node_limits": [
                    {
                        "pipeline_key": "question_content",
                        "node_key": "fetch_question_context",
                        "concurrency_limit": 3,
                    },
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["workspace"]["name"] == "Updated Workspace"
    assert body["settings"]["entityType"] == "video"
    assert body["executor_configuration"]["allocations"] == [
        {"executor_id": "local-default", "workspace_id": "default", "concurrency_limit": 4},
    ]
    assert body["executor_configuration"]["bindings"] == [
        {
            "pipeline_key": "question_content",
            "node_key": "fetch_question_context",
            "executor_id": "local-default",
        },
    ]
    assert body["executor_configuration"]["node_limits"] == [
        {
            "pipeline_key": "question_content",
            "node_key": "fetch_question_context",
            "concurrency_limit": 3,
        },
    ]


def test_workspace_configuration_rejects_invalid_binding_without_partial_update(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        original = c.get("/api/workspaces/default").json()["workspace"]
        response = c.put(
            "/api/workspaces/default/configuration",
            json={
                "name": "Must Roll Back",
                "settings": {"pipelineKey": "question_content"},
                "executor_allocations": [
                    {"executor_id": "local-default", "concurrency_limit": 4},
                ],
                "node_bindings": [
                    {
                        "pipeline_key": "question_content",
                        "node_key": "unknown_node",
                        "executor_id": "local-default",
                    },
                ],
                "node_limits": [],
            },
        )
        persisted = c.get("/api/workspaces/default").json()["workspace"]

    assert response.status_code == 400
    assert persisted["name"] == original["name"]
    config = app.state.job_db.get_workspace_executor_configuration("default")
    # Startup bootstrap materialized reading_analysis defaults for the default
    # workspace; the failed PUT rolls back to that state.
    assert config["allocations"] == [
        {"workspace_id": "default", "executor_id": "local-default", "concurrency_limit": 1}
    ]
    assert config["bindings"] == [
        {
            "pipeline_key": "reading_analysis",
            "node_key": "clean_and_parse",
            "executor_id": "local-default",
        },
        {
            "pipeline_key": "reading_analysis",
            "node_key": "fetch_questions",
            "executor_id": "local-default",
        },
    ]
    assert config["node_limits"] == [
        {
            "pipeline_key": "reading_analysis",
            "node_key": "clean_and_parse",
            "concurrency_limit": 1,
        },
        {
            "pipeline_key": "reading_analysis",
            "node_key": "fetch_questions",
            "concurrency_limit": 1,
        },
    ]


def test_workspace_job_batch_stores_normalized_source_payload(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
        "server.app.services.job_intake.list_questions_by_knowledge",
        fake_list_questions_by_knowledge,
    )
    monkeypatch.setattr("server.app.services.job_intake.get_token", lambda env, config: "token")

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
        "server.app.services.job_intake.list_questions_by_knowledge",
        fake_list_questions_by_knowledge,
    )
    monkeypatch.setattr("server.app.services.job_intake.get_token", lambda env, config: "token")

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    assert runs[0]["run_dir"] == str(tmp_path / "run-1")
    assert runs[0]["session_dir"] == str(tmp_path / "run-1" / "session")
    assert json.loads(runs[0]["command_json"])[0] == "pi"


def test_get_pipeline_definition_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        response = c.get("/api/pipelines/question_content")

    assert response.status_code == 200
    body = response.json()
    assert body["pipeline"]["key"] == "question_content"
    assert body["pipeline"]["label"] == "题目内容生成"
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
    assert all("label" in node for node in body["pipeline"]["nodes"])
    fetch_node = next(
        node for node in body["pipeline"]["nodes"] if node["key"] == "fetch_question_context"
    )
    assert fetch_node["label"] == "获取题目上下文"
    graph_node = next(
        node for node in body["pipeline"]["nodes"] if node["key"] == "content_graph_generation"
    )
    assert graph_node["after"] == ["solution_decomposition"]


def test_list_pipelines_includes_registered_pipelines(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        response = c.get("/api/pipelines")

    assert response.status_code == 200
    body = response.json()
    assert any(p["key"] for p in body["pipelines"])


def test_create_workspace_job_batch_rejects_empty_question_ids(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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


def test_job_detail_includes_node_dependencies(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    assert all("label" in node for node in response.json()["nodes"])
    nodes = {node["node_key"]: node for node in response.json()["nodes"]}
    assert nodes["content_graph_generation"]["after"] == ["solution_decomposition"]


def test_workspace_stats_hidden_when_pipelines_disabled(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = False
    with TestClient(app) as c:
        response = c.get("/api/workspaces/default/stats")
    assert response.status_code == 404


def test_workspace_stats_returns_counts_and_executor_status(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        ws = c.post("/api/workspaces", json={"name": "Stats WS"}).json()
        ws_id = ws["workspace"]["id"]
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "pipeline_key": "reading_analysis",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q301", "Q302"],
                "knowledge_codes": [],
            },
        )
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    assert body["workspace_id"] == ws_id
    assert body["name"] == "Stats WS"
    assert body["pipeline_key"] == "reading_analysis"
    assert body["pipeline_label"] == "题目审题分析 Pipeline"
    assert body["job_stats"]["pending"] == 2
    assert "queued" not in body["job_stats"]
    assert body["executor_status"]["executors"] == []
    assert body["latest_run"] is None


def test_workspace_stats_executor_status_reflects_allocations_and_leases(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    job_db = app.state.job_db

    with TestClient(app) as c:
        ws = c.post("/api/workspaces", json={"name": "Stats WS"}).json()
        ws_id = ws["workspace"]["id"]
        job_db.replace_workspace_executor_configuration(
            ws_id,
            allocations=[{"executor_id": "local-default", "concurrency_limit": 4}],
            bindings=[
                {
                    "pipeline_key": "reading_analysis",
                    "node_key": "review_keywords",
                    "executor_id": "local-default",
                }
            ],
            node_limits=[],
        )
        c.post(
            f"/api/workspaces/{ws_id}/job-batches",
            json={
                "pipeline_key": "reading_analysis",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q301"],
                "knowledge_codes": [],
            },
        )
        stats = c.get(f"/api/workspaces/{ws_id}/stats")

    assert stats.status_code == 200
    body = stats.json()
    executors = body["executor_status"]["executors"]
    assert len(executors) == 1
    assert executors[0]["executor_id"] == "local-default"
    assert executors[0]["kind"] == "local"
    assert executors[0]["global_capacity"] == 16
    assert executors[0]["workspace_limit"] == 4
    assert executors[0]["running"] == 0
    assert executors[0]["available"] == 4
    assert executors[0]["binding_count"] == 1


def test_workspace_stats_latest_run_reflects_node_runs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = False
    with TestClient(app) as c:
        response = c.delete("/api/workspaces/some_ws")
    assert response.status_code == 404


def test_delete_workspace_rejects_default(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        response = c.delete("/api/workspaces/default")
    assert response.status_code == 400
    assert "default" in response.json()["detail"].lower()


def test_delete_workspace_rejects_when_jobs_running(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        resp = c.delete("/api/jobs/nonexistent")
    assert resp.status_code == 404


def test_delete_job_rejects_running_job(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
        # Start a node run so _job_has_running_nodes returns True
        app.state.job_db.start_node_run(job_id, "fetch_question_context", ["cmd"], str(log_path))
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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


def test_workspace_batch_delete_removes_jobs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["job_id"] == job_id
    assert results[0]["operation"] == "delete"
    assert results[0]["status"] == "succeeded"
    assert detail.status_code == 404


def test_workspace_stats_returns_404_for_unknown_workspace(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        resp = c.get("/api/workspaces/nonexistent/stats")
    assert resp.status_code == 404


def test_delete_workspace_cascades_and_returns_deleted_id(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "reading_analysis",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001", "Q002"],
                "knowledge_codes": [],
            },
        )
        response = c.get("/api/workspaces/default/dag")

    assert response.status_code == 200
    body = response.json()
    assert body["pipeline"]["key"] == "reading_analysis"
    assert all("label" in node for node in body["nodes"])
    first = body["nodes"][0]
    assert first["key"] == "fetch_questions"
    assert first["status_counts"]["pending"] == 2


def test_get_resource_providers_returns_provider_list(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    app.state.settings.config["cms"] = {}
    with TestClient(app) as c:
        response = c.post("/api/workspaces/default/settings/test-connection")

    assert response.status_code == 400
    assert "Global CMS URL" in response.json()["detail"]


def test_job_batch_rejects_disabled_resource_provider(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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


def test_reading_analysis_batch_by_ids_creates_one_job_per_question(client):
    response = client.post(
        "/api/workspaces/default/job-batches",
        json={
            "pipeline_key": "reading_analysis",
            "source_kind": "batch_by_ids",
            "question_ids": ["Q1", "Q2", "Q1"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created_count"] == 2
    assert {job["source_id"] for job in body["jobs"]} == {"Q1", "Q2"}
    assert all(job["pipeline_key"] == "reading_analysis" for job in body["jobs"])


def test_reading_analysis_batch_by_knowledge_resolves_questions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.cms.question import CmsQuestionSummary
    from server.app.main import create_app

    calls = []

    def fake_list_questions_by_knowledge(code, api_url=None, token=None):
        calls.append({"code": code, "api_url": api_url, "token": token})
        return [
            CmsQuestionSummary("Q1", "题目一", {"uuid": "Q1"}),
            CmsQuestionSummary("Q2", "题目二", {"uuid": "Q2"}),
        ]

    monkeypatch.setattr(
        "server.app.services.job_intake.list_questions_by_knowledge",
        fake_list_questions_by_knowledge,
    )
    monkeypatch.setattr("server.app.services.job_intake.get_token", lambda env, config: "token")

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "question_list_url": "https://cms.example/question/list?bank_version=v5&page_size=50",
    }
    with TestClient(app) as c:
        response = c.post(
            "/api/workspaces/default/job-batches",
            json={
                "pipeline_key": "reading_analysis",
                "source_kind": "batch_by_knowledge",
                "question_ids": [],
                "knowledge_codes": ["K001", "K001", " K002 "],
            },
        )

    assert response.status_code == 200
    body = response.json()
    payload = json.loads(body["batch"]["source_payload_json"])
    assert [call["code"] for call in calls] == ["K001", "K002"]
    assert payload["knowledge_codes"] == ["K001", "K002"]
    assert payload["question_ids"] == ["Q1", "Q2"]
    assert body["created_count"] == 2
    assert [job["source_type"] for job in body["jobs"]] == ["question", "question"]
    assert [job["title"] for job in body["jobs"]] == ["题目一", "题目二"]
    assert all(job["pipeline_key"] == "reading_analysis" for job in body["jobs"])


def test_update_workspace_rejects_invalid_pipeline_key(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        create_resp = c.post("/api/workspaces", json={"name": "Test"})
        assert create_resp.status_code == 200, create_resp.text
        ws_id = create_resp.json()["workspace"]["id"]
        resp = c.patch(
            f"/api/workspaces/{ws_id}",
            json={"default_pipeline_key": "nonexistent"},
        )

    assert resp.status_code == 404
    assert "Unknown pipeline" in resp.json()["detail"]


def test_batch_rerun_skips_not_found_and_running_jobs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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


def test_batch_delete_skips_not_found_and_running_jobs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
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


def test_get_artifact_returns_404(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        # Job not found
        resp = c.get("/api/jobs/nonexistent/artifacts/test.json")
    assert resp.status_code == 404


def test_get_job_run_log_returns_redacted_tail(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    app.state.settings.config["secret_token"] = "leaked-token"
    log_dir = app.state.settings.logs_dir / "jobs"
    log_dir.mkdir(parents=True, exist_ok=True)

    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q1"],
                "knowledge_codes": [],
            },
        )
        job_id = "test_question_content_Q1"
        log_path = log_dir / f"{job_id}-fetch_question_context.log"
        log_path.write_text("start\nleaked-token\nend\n", encoding="utf-8")
        run = app.state.job_db.start_node_run(
            job_id, "fetch_question_context", ["cmd"], str(log_path)
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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


def test_rerun_node_errors(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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


def test_batch_delete_skips_running_job(tmp_path):

    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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


def test_reject_invalid_job_subpath(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        # Job not found
        resp = c.get("/api/jobs/nonexistent/invalid/path")
    assert resp.status_code == 404


def test_rerun_node_cleanup_failed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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


def test_job_detail_includes_node_inputs_outputs(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True

    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "WS"})
        batch = c.post(
            "/api/workspaces/ws/job-batches",
            json={
                "pipeline_key": "question_content",
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


def test_app_startup_materializes_executor_configuration_for_default_workspace(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.jobs import JobQueries
    from server.app.main import create_app
    from tests.helpers import ensure_legacy_workspace_tables

    db_path = tmp_path / "video_hive.sqlite"
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    queries = JobQueries(db_path, jobs_dir=jobs_dir)
    ensure_legacy_workspace_tables(queries)
    with queries.connect() as conn:
        conn.execute(
            "insert into workspace_agent_assignments(workspace_id, agent_id, concurrency_limit) values (?, ?, ?)",
            ("default", "pi", 3),
        )

    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as c:
        response = c.get("/api/workspaces/default/executor-configuration")

    assert response.status_code == 200
    assert {row["executor_id"] for row in response.json()["allocations"]} == {
        "local-default",
        "pi-default",
    }


def test_rerun_node_preserves_ancestors(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q702"],
                "knowledge_codes": [],
            },
        )
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "reading_analysis",
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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
                    id, execution_id, executor_id, workspace_id, job_id, pipeline_key,
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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
                    id, execution_id, executor_id, workspace_id, job_id, pipeline_key,
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
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Test"})
        c.post(
            "/api/workspaces/test/job-batches",
            json={
                "pipeline_key": "question_content",
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


def test_run_to_target_sets_execution_control(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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
    app.state.settings.executor_runtime.pipelines.enabled = True
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


def test_batch_run_to_returns_results_in_order(tmp_path):
    from fastapi.testclient import TestClient

    from server.app.main import create_app

    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.executor_runtime.pipelines.enabled = True
    with TestClient(app) as c:
        created = c.post(
            "/api/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q805"],
                "knowledge_codes": [],
            },
        ).json()
        job_id = created["jobs"][0]["id"]
        response = c.post(
            "/api/workspaces/default/jobs/batch-run-to",
            json={"job_ids": [job_id, "missing-job"], "target_node_key": "question_understanding"},
        )

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert results[0]["job_id"] == job_id
    assert results[0]["status"] == "succeeded"
    assert results[1]["job_id"] == "missing-job"
    assert results[1]["status"] == "failed"
