from unittest.mock import patch

from fastapi.testclient import TestClient

from server.app.main import create_app
from tests.helpers.auth import authenticate_client
from workspace_libs.cms.client import CmsClientError


class _FakeConnectionTokens:
    """Stand-in for ConnectionTokenService: serves a fixed runtime config."""

    def __init__(self, config):
        self._config = config

    def __call__(self, *args, **kwargs):
        return self

    def runtime_config(self, key):
        return dict(self._config)


_CMS_RUNTIME_CONFIG = {
    "api_url": "https://cms.example/question/detail",
    "token": "token",
}


def test_question_detail_success(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("workflows", {})["enabled"] = True

    fake_payload = {
        "code": 0,
        "data": {
            "question_uuid": "Q001",
            "question_title": "Test Question",
            "body": {"content": "What is 2+2?"},
            "option": [
                {"label": "A", "content": "3"},
                {"label": "B", "content": "4"},
            ],
            "answer": [[{"content": "B"}]],
            "analyze": [[{"content": "Basic arithmetic.", "title": "", "step": 0}]],
        },
    }

    with (
        authenticate_client(TestClient(app)) as c,
        patch(
            "server.app.services.question_detail.ConnectionTokenService",
            _FakeConnectionTokens(_CMS_RUNTIME_CONFIG),
        ),
        patch("workspace_libs.cms.question._fetch_json", lambda url, params, token: fake_payload),
    ):
        c.post(
            "/api/workspaces",
            json={
                "name": "Math",
                "default_workflow_key": "question_comprehension_info",
            },
        )
        c.post(
            "/api/workspaces/math/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
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
    app.state.settings.config.setdefault("workflows", {})["enabled"] = True
    with authenticate_client(TestClient(app)) as c:
        response = c.get("/api/workspaces/nonexistent/questions/Q001")
    assert response.status_code == 404


def test_question_detail_cms_failure(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("workflows", {})["enabled"] = True

    def fake_fetch_question_detail(question_id, api_url, token):
        raise CmsClientError("CMS down")

    with (
        authenticate_client(TestClient(app)) as c,
        patch(
            "server.app.services.question_detail.ConnectionTokenService",
            _FakeConnectionTokens(_CMS_RUNTIME_CONFIG),
        ),
        patch(
            "server.app.services.question_detail.fetch_question_detail", fake_fetch_question_detail
        ),
    ):
        c.post(
            "/api/workspaces",
            json={
                "name": "Math",
                "default_workflow_key": "question_comprehension_info",
            },
        )
        response = c.get("/api/workspaces/math/questions/Q001")

    assert response.status_code == 502


def test_question_detail_without_connection_returns_empty_normalized(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("workflows", {})["enabled"] = True
    monkeypatch.setattr(
        "server.app.services.question_detail.workspace_node_connection_key",
        lambda *args, **kwargs: "",
    )

    with authenticate_client(TestClient(app)) as c:
        # Intake is node-phase now: it builds opaque candidates without calling
        # the CMS, so no intake mocking is needed. This test only checks the
        # detail endpoint's empty-config behavior.
        c.post(
            "/api/workspaces",
            json={"name": "Math", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/math/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
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


def test_question_detail_token_only_connection_degrades_to_local(tmp_path, monkeypatch):
    """Connection without base_url/api_url must not 502 the detail page."""
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("workflows", {})["enabled"] = True

    def fail_fetch(*args, **kwargs):  # must not be called without a URL
        raise AssertionError("fetch_question_detail called without an api_url")

    with (
        authenticate_client(TestClient(app)) as c,
        patch(
            "server.app.services.question_detail.ConnectionTokenService",
            _FakeConnectionTokens({"token": "token"}),
        ),
        patch("server.app.services.question_detail.fetch_question_detail", fail_fetch),
    ):
        c.post(
            "/api/workspaces",
            json={"name": "Math", "default_workflow_key": "question_comprehension_info"},
        )
        c.post(
            "/api/workspaces/math/job-batches",
            json={
                "workflow_key": "question_comprehension_info",
                "source_kind": "batch_by_ids",
                "question_ids": ["Q001"],
                "knowledge_codes": [],
            },
        )
        response = c.get("/api/workspaces/math/questions/Q001")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Q001"
    assert body["normalized"]["stem"] is None


def test_question_detail_parses_nested_answer_and_analysis(tmp_path, monkeypatch):
    app = create_app(data_dir=tmp_path, start_worker=False)
    app.state.settings.config.setdefault("workflows", {})["enabled"] = True

    def fake_fetch_json(url, params, token):
        return {
            "code": 0,
            "message": "success",
            "data": {
                "question_uuid": "Q001",
                "body": {"content": "<p>Fill in ___1___ and ___2___</p>"},
                "answer": [
                    [{"content": "A1", "is_latex": 1}, {"content": "A2", "is_latex": 0}],
                    [{"content": "B1", "is_latex": 0}],
                ],
                "analyze": [
                    [
                        {"content": "<p>Step 0</p>", "title": "<p>Title 0</p>", "step": 0},
                        {"content": "<p>Step 1</p>", "title": "", "step": 1},
                    ]
                ],
            },
        }

    monkeypatch.setattr(
        "workspace_libs.cms.question._fetch_json",
        fake_fetch_json,
    )
    monkeypatch.setattr(
        "server.app.services.question_detail.ConnectionTokenService",
        _FakeConnectionTokens(_CMS_RUNTIME_CONFIG),
    )

    with authenticate_client(TestClient(app)) as c:
        c.post(
            "/api/workspaces",
            json={
                "name": "Math",
                "default_workflow_key": "question_comprehension_info",
            },
        )
        response = c.get("/api/workspaces/math/questions/Q001")

    assert response.status_code == 200
    body = response.json()
    blanks = body["normalized"]["answer_blanks"]
    assert len(blanks) == 2
    assert len(blanks[0]["alternatives"]) == 2
    assert blanks[0]["is_latex"] is True
    assert blanks[0]["alternatives"] == ["A1", "A2"]
    assert blanks[1]["alternatives"] == ["B1"]
    steps = body["normalized"]["analysis_steps"]
    assert len(steps) == 1
    assert len(steps[0]) == 2
    assert steps[0][0]["content"] == "<p>Step 0</p>"
    assert steps[0][0]["title"] == "<p>Title 0</p>"
    assert steps[0][1]["step"] == 1
