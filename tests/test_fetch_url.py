import requests

from server.app.cms.client import get_token
from server.app.cms.knowledge import lookup_knowledge_video
from server.app.cms.question import lookup_question_video


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_get_token_uses_configured_token_generator(monkeypatch):
    calls = []
    monkeypatch.delenv("BASECMS_TOKEN", raising=False)

    def fake_post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse({"data": {"token": "generated-token"}})

    monkeypatch.setattr(requests, "post", fake_post)

    token = get_token(
        "prod",
        {
            "token_gen": {
                "app_id": "app-from-config",
                "nonce": "nonce-from-config",
                "secret": "secret-from-config",
                "url": "http://token.example.test",
            }
        },
    )

    assert token == "generated-token"
    assert calls[0]["url"] == "http://token.example.test"
    assert calls[0]["json"]["app_id"] == "app-from-config"
    assert calls[0]["json"]["nonce"] == "nonce-from-config"
    assert calls[0]["json"]["secret"] == "secret-from-config"


def test_get_token_returns_none_without_configured_credentials(monkeypatch):
    monkeypatch.delenv("BASECMS_TOKEN", raising=False)
    monkeypatch.delenv("BASECMS_APP_ID", raising=False)
    monkeypatch.delenv("BASECMS_NONCE", raising=False)
    monkeypatch.delenv("BASECMS_SECRET", raising=False)
    monkeypatch.delenv("BASECMS_TOKEN_URL", raising=False)

    assert get_token("prod", {}) is None


def test_lookup_knowledge_video_found_with_url(monkeypatch):
    payload = {
        "data": {
            "knowledge_code": "K001",
            "knowledge_name": "奇函数",
            "resource": [
                {
                    "resource_type": 1,
                    "video_data": {"source_v2": "https://example.com/k001.mp4"},
                }
            ],
        }
    }

    monkeypatch.setattr("server.app.cms.knowledge._fetch_json", lambda *args, **kwargs: payload)

    result = lookup_knowledge_video("K001", "https://cms.example/knowledge", "token")

    assert result.status == "found"
    assert result.url == "https://example.com/k001.mp4"
    assert result.title == "奇函数"


def test_lookup_knowledge_video_found_without_url(monkeypatch):
    payload = {"data": {"knowledge_code": "K001", "knowledge_name": "奇函数", "resource": []}}
    monkeypatch.setattr("server.app.cms.knowledge._fetch_json", lambda *args, **kwargs: payload)

    result = lookup_knowledge_video("K001", "https://cms.example/knowledge", "token")

    assert result.status == "missing_url"
    assert result.url == ""
    assert result.title == "奇函数"


def test_lookup_knowledge_video_not_found(monkeypatch):
    monkeypatch.setattr("server.app.cms.knowledge._fetch_json", lambda *args, **kwargs: {"data": None})

    result = lookup_knowledge_video("K404", "https://cms.example/knowledge", "token")

    assert result.status == "not_found"
    assert result.url == ""


def test_lookup_question_video_found_with_url(monkeypatch):
    payload = {
        "data": {
            "question_uuid": "Q001",
            "title": "题目一",
            "video_data": [{"source": "https://example.com/q001.mp4"}],
        }
    }
    monkeypatch.setattr("server.app.cms.question._fetch_json", lambda *args, **kwargs: payload)

    result = lookup_question_video("Q001", "https://cms.example/question", "token")

    assert result.status == "found"
    assert result.url == "https://example.com/q001.mp4"
    assert result.title == "题目一"


def test_lookup_question_video_found_without_url(monkeypatch):
    payload = {"data": {"question_uuid": "Q001", "title": "题目一", "video_data": []}}
    monkeypatch.setattr("server.app.cms.question._fetch_json", lambda *args, **kwargs: payload)
    result = lookup_question_video("Q001", "https://cms.example/question", "token")
    assert result.status == "missing_url"
    assert result.url == ""
    assert result.title == "题目一"


def test_lookup_question_video_not_found(monkeypatch):
    monkeypatch.setattr("server.app.cms.question._fetch_json", lambda *args, **kwargs: {"data": {}})

    result = lookup_question_video("Q404", "https://cms.example/question", "token")

    assert result.status == "not_found"
    assert result.url == ""


def test_lookup_question_video_nonempty_error_payload_is_not_found(monkeypatch):
    payload = {"data": {"message": "question not found", "code": 404}}
    monkeypatch.setattr("server.app.cms.question._fetch_json", lambda *args, **kwargs: payload)

    result = lookup_question_video("Q404", "https://cms.example/question", "token")

    assert result.status == "not_found"
    assert result.url == ""


def test_lookup_knowledge_video_extracts_source_uuid_from_video_data(monkeypatch):
    payload = {
        "data": {
            "knowledge_code": "K001",
            "knowledge_name": "奇函数",
            "resource": [
                {
                    "resource_type": 1,
                    "video_data": {
                        "source_v2": "https://example.com/k001.mp4",
                        "source_uuid": "uuid-k001-abc",
                    },
                }
            ],
        }
    }
    monkeypatch.setattr("server.app.cms.knowledge._fetch_json", lambda *args, **kwargs: payload)
    result = lookup_knowledge_video("K001", "https://cms.example/knowledge", "token")
    assert result.status == "found"
    assert result.url == "https://example.com/k001.mp4"
    assert result.source_uuid == "uuid-k001-abc"


def test_lookup_knowledge_video_source_uuid_empty_when_not_present(monkeypatch):
    payload = {
        "data": {
            "knowledge_code": "K001",
            "knowledge_name": "奇函数",
            "resource": [
                {
                    "resource_type": 1,
                    "video_data": {"source_url": "https://example.com/k001.mp4"},
                }
            ],
        }
    }
    monkeypatch.setattr("server.app.cms.knowledge._fetch_json", lambda *args, **kwargs: payload)
    result = lookup_knowledge_video("K001", "https://cms.example/knowledge", "token")
    assert result.status == "found"
    assert result.source_uuid == ""


def test_lookup_question_video_extracts_source_uuid_from_video_data(monkeypatch):
    payload = {
        "data": {
            "question_uuid": "Q001",
            "title": "题目一",
            "video_data": [
                {
                    "source": "https://example.com/q001.mp4",
                    "source_uuid": "uuid-q001-xyz",
                }
            ],
        }
    }
    monkeypatch.setattr("server.app.cms.question._fetch_json", lambda *args, **kwargs: payload)
    result = lookup_question_video("Q001", "https://cms.example/question", "token")
    assert result.status == "found"
    assert result.url == "https://example.com/q001.mp4"
    assert result.source_uuid == "uuid-q001-xyz"


def test_lookup_question_video_source_uuid_empty_when_not_present(monkeypatch):
    payload = {
        "data": {
            "question_uuid": "Q001",
            "title": "题目一",
            "video_data": [{"source": "https://example.com/q001.mp4"}],
        }
    }
    monkeypatch.setattr("server.app.cms.question._fetch_json", lambda *args, **kwargs: payload)
    result = lookup_question_video("Q001", "https://cms.example/question", "token")
    assert result.status == "found"
    assert result.source_uuid == ""
