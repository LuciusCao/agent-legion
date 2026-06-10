from fastapi.testclient import TestClient

from server.app.cms.question import CmsQuestionDetail
from server.app.main import create_app


def test_question_detail_success(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "question_detail_url": "https://cms.example/question/detail",
    }

    def fake_fetch_question_detail(question_id, api_url, token):
        return CmsQuestionDetail(
            question_id=question_id,
            title="Test Question",
            normalized={
                "stem": "What is 2+2?",
                "options": [
                    {"label": "A", "content": "3"},
                    {"label": "B", "content": "4"},
                ],
                "answer": ["B"],
                "analysis": "Basic arithmetic.",
            },
            payload={"code": 0, "data": {"question_uuid": question_id}},
        )

    monkeypatch.setattr(
        "server.app.routes.questions.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.routes.questions.get_token",
        lambda env, config: "token",
    )

    with TestClient(app) as c:
        c.post(
            "/api/workspaces",
            json={
                "name": "Math",
                "cms_config": {"question_detail_url": "https://cms.example/question/detail"},
            },
        )
        c.post(
            "/api/workspaces/math/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
            },
        )
        response = c.get("/api/workspaces/math/questions/Q001")

    assert response.status_code == 200
    body = response.json()
    assert body["question_id"] == "Q001"
    assert body["title"] == "Test Question"
    assert body["normalized"]["stem"] == "What is 2+2?"
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["source_id"] == "Q001"


def test_question_detail_workspace_not_found(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    with TestClient(app) as c:
        response = c.get("/api/workspaces/nonexistent/questions/Q001")
    assert response.status_code == 404


def test_question_detail_cms_failure(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True
    app.state.settings.config["cms"] = {
        "env": "prod",
        "question_detail_url": "https://cms.example/question/detail",
    }

    def fake_fetch_question_detail(question_id, api_url, token):
        raise RuntimeError("CMS down")

    monkeypatch.setattr(
        "server.app.routes.questions.fetch_question_detail",
        fake_fetch_question_detail,
    )
    monkeypatch.setattr(
        "server.app.routes.questions.get_token",
        lambda env, config: "token",
    )

    with TestClient(app) as c:
        c.post(
            "/api/workspaces",
            json={
                "name": "Math",
                "cms_config": {"question_detail_url": "https://cms.example/question/detail"},
            },
        )
        response = c.get("/api/workspaces/math/questions/Q001")

    assert response.status_code == 502


def test_question_detail_no_cms_config_returns_empty_normalized(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("pipelines", {})["enabled"] = True

    with TestClient(app) as c:
        c.post("/api/workspaces", json={"name": "Math"})
        c.post(
            "/api/workspaces/math/job-batches",
            json={
                "pipeline_key": "question_content",
                "source_kind": "direct_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
            },
        )
        response = c.get("/api/workspaces/math/questions/Q001")

    assert response.status_code == 200
    body = response.json()
    assert body["question_id"] == "Q001"
    assert body["title"] == "Q001"
    assert body["normalized"]["stem"] is None
    assert len(body["jobs"]) == 1
