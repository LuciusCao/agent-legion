import json


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
