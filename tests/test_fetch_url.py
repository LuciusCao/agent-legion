import pytest
import requests

from server.app.cms.client import CmsClientError, get_token
from server.app.cms.knowledge import lookup_knowledge_video
from server.app.cms.question import (
    fetch_question_detail,
    list_questions_by_knowledge,
    lookup_question_video,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_get_token_uses_configured_token_generator(monkeypatch):
    calls = []
    for env_key in (
        "CMS_TOKEN",
        "CMS_APP_ID",
        "CMS_NONCE",
        "CMS_SECRET",
        "CMS_TOKEN_URL",
        "BASECMS_TOKEN",
        "BASECMS_APP_ID",
        "BASECMS_NONCE",
        "BASECMS_SECRET",
        "BASECMS_TOKEN_URL",
    ):
        monkeypatch.delenv(env_key, raising=False)

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
    monkeypatch.delenv("CMS_TOKEN", raising=False)
    monkeypatch.delenv("CMS_APP_ID", raising=False)
    monkeypatch.delenv("CMS_NONCE", raising=False)
    monkeypatch.delenv("CMS_SECRET", raising=False)
    monkeypatch.delenv("CMS_TOKEN_URL", raising=False)
    monkeypatch.delenv("BASECMS_TOKEN", raising=False)
    monkeypatch.delenv("BASECMS_APP_ID", raising=False)
    monkeypatch.delenv("BASECMS_NONCE", raising=False)
    monkeypatch.delenv("BASECMS_SECRET", raising=False)
    monkeypatch.delenv("BASECMS_TOKEN_URL", raising=False)

    assert get_token("prod", {}) is None


def test_get_token_binding_token_wins_over_env(monkeypatch):
    monkeypatch.setenv("CMS_TOKEN", "env-token")

    token = get_token("dev", {"token": "binding-token", "token_from_binding": True})

    assert token == "binding-token"


def test_get_token_env_wins_without_binding_marker(monkeypatch):
    monkeypatch.setenv("CMS_TOKEN", "env-token")

    assert get_token("dev", {"token": "config-token"}) == "env-token"


def test_get_token_marker_without_token_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("CMS_TOKEN", "env-token")

    assert get_token("dev", {"token_from_binding": True}) == "env-token"


def test_lookup_knowledge_video_requires_configured_url():
    with pytest.raises(CmsClientError, match=r"cms\.base_url"):
        lookup_knowledge_video("K001")


def test_lookup_question_video_requires_configured_url():
    with pytest.raises(CmsClientError, match=r"cms\.base_url"):
        lookup_question_video("Q001")


def test_list_questions_by_knowledge_requires_configured_url():
    with pytest.raises(CmsClientError, match=r"cms\.base_url"):
        list_questions_by_knowledge("K001")


def test_fetch_question_detail_requires_configured_url():
    with pytest.raises(CmsClientError, match=r"cms\.base_url"):
        fetch_question_detail("Q001")


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
    monkeypatch.setattr(
        "server.app.cms.knowledge._fetch_json", lambda *args, **kwargs: {"data": None}
    )

    result = lookup_knowledge_video("K404", "https://cms.example/knowledge", "token")

    assert result.status == "not_found"
    assert result.url == ""


def test_lookup_question_video_found_with_url(monkeypatch):
    payload = {
        "data": {
            "question_uuid": "Q001",
            "body": {"content": "题目一"},
            "video_data": [{"source": "https://example.com/q001.mp4"}],
        }
    }
    monkeypatch.setattr("server.app.cms.question._fetch_json", lambda *args, **kwargs: payload)

    result = lookup_question_video("Q001", "https://cms.example/question", "token")

    assert result.status == "found"
    assert result.url == "https://example.com/q001.mp4"
    assert result.title == "题目一"


def test_lookup_question_video_found_without_url(monkeypatch):
    payload = {"data": {"question_uuid": "Q001", "body": {"content": "题目一"}, "video_data": []}}
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
            "body": {"content": "题目一"},
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
            "body": {"content": "题目一"},
            "video_data": [{"source": "https://example.com/q001.mp4"}],
        }
    }
    monkeypatch.setattr("server.app.cms.question._fetch_json", lambda *args, **kwargs: payload)
    result = lookup_question_video("Q001", "https://cms.example/question", "token")
    assert result.status == "found"
    assert result.source_uuid == ""


def test_list_questions_by_knowledge_fetches_all_pages(monkeypatch):
    calls = []
    payloads = [
        {
            "data": {
                "question_list": [
                    {"question_uuid": "Q001", "body": {"content": "题目一"}},
                    {"question_uuid": "Q002", "body": {"content": "题目二"}},
                ],
                "total": 3,
            }
        },
        {
            "data": {
                "question_list": [
                    {"question_uuid": "Q003", "body": {"content": "题目三"}},
                ],
                "total": 3,
            }
        },
    ]

    def fake_fetch(url, params, token):
        calls.append({"url": url, "params": params, "token": token})
        return payloads[len(calls) - 1]

    monkeypatch.setattr("server.app.cms.question._fetch_json", fake_fetch)

    result = list_questions_by_knowledge(
        "K001",
        "https://cms.example/question/list?bank_version=v5&page_size=2",
        "token",
    )

    assert [item.question_id for item in result] == ["Q001", "Q002", "Q003"]
    assert [item.title for item in result] == ["题目一", "题目二", "题目三"]
    assert calls[0]["params"] == {"knowledge": "K001", "page": 1}
    assert calls[1]["params"] == {"knowledge": "K001", "page": 2}


def test_list_questions_by_knowledge_strips_dynamic_query_params(monkeypatch):
    calls = []
    payload = {
        "data": {
            "question_list": [{"question_uuid": "Q001", "body": {"content": "题目一"}}],
            "total": 1,
        }
    }

    def fake_fetch(url, params, token):
        calls.append({"url": url, "params": params, "token": token})
        return payload

    monkeypatch.setattr("server.app.cms.question._fetch_json", fake_fetch)

    list_questions_by_knowledge(
        "K001",
        "https://cms.example/question/list?bank_version=v5&knowledge=OLD&page=1&page_size=50",
        "token",
    )

    assert calls[0]["url"] == "https://cms.example/question/list?bank_version=v5&page_size=50"
    assert calls[0]["params"] == {"knowledge": "K001", "page": 1}


def test_fetch_question_detail_returns_structured_context(monkeypatch):
    payload = {
        "data": {
            "question_uuid": "Q001",
            "body": {"content": "1 + 1 = ?"},
            "option": [{"key": "A", "content": "2"}],
            "analyze": "加法",
        }
    }
    monkeypatch.setattr("server.app.cms.question._fetch_json", lambda *args, **kwargs: payload)

    result = fetch_question_detail("Q001", "https://cms.example/question/detail", "token")

    assert result.question_id == "Q001"
    assert result.title == "1 + 1 = ?"
    assert result.normalized["stem"] == "1 + 1 = ?"
    assert result.normalized["options"] == [{"key": "A", "content": "2"}]
    assert result.payload == payload
