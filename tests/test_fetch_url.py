"""Tests for workspace_libs CMS fetch helpers (URL handling, parsing, errors)."""

import pytest
import requests

from workspace_libs.cms import client as cms_client
from workspace_libs.cms.client import CmsClientError, get_token
from workspace_libs.cms.knowledge import lookup_knowledge_video
from workspace_libs.cms.question import (
    fetch_question_detail,
    list_questions_by_knowledge,
    lookup_question_video,
)


def test_get_token_reads_config_token_only(monkeypatch):
    # The env CMS_TOKEN channel is retired: the connection layer resolves the
    # token into the node config in memory at dispatch; get_token only reads
    # it back.
    monkeypatch.setenv("CMS_TOKEN", "env-token")

    assert get_token("dev", {"token": "config-token"}) == "config-token"


def test_get_token_returns_none_without_config_token(monkeypatch):
    monkeypatch.setenv("CMS_TOKEN", "env-token")

    assert get_token("prod", {}) is None
    assert get_token("prod", None) is None
    assert get_token("prod", {"token": "  "}) is None


def test_lookup_knowledge_video_requires_configured_url():
    with pytest.raises(CmsClientError, match="not configured"):
        lookup_knowledge_video("K001")


def test_lookup_question_video_requires_configured_url():
    with pytest.raises(CmsClientError, match="not configured"):
        lookup_question_video("Q001")


def test_list_questions_by_knowledge_requires_configured_url():
    with pytest.raises(CmsClientError, match="not configured"):
        list_questions_by_knowledge("K001")


def test_fetch_question_detail_requires_configured_url():
    with pytest.raises(CmsClientError, match="not configured"):
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

    monkeypatch.setattr("workspace_libs.cms.knowledge._fetch_json", lambda *args, **kwargs: payload)

    result = lookup_knowledge_video("K001", "https://cms.example/knowledge", "token")

    assert result.status == "found"
    assert result.url == "https://example.com/k001.mp4"
    assert result.title == "奇函数"


def test_lookup_knowledge_video_found_without_url(monkeypatch):
    payload = {"data": {"knowledge_code": "K001", "knowledge_name": "奇函数", "resource": []}}
    monkeypatch.setattr("workspace_libs.cms.knowledge._fetch_json", lambda *args, **kwargs: payload)

    result = lookup_knowledge_video("K001", "https://cms.example/knowledge", "token")

    assert result.status == "missing_url"
    assert result.url == ""
    assert result.title == "奇函数"


def test_lookup_knowledge_video_not_found(monkeypatch):
    monkeypatch.setattr(
        "workspace_libs.cms.knowledge._fetch_json", lambda *args, **kwargs: {"data": None}
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
    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", lambda *args, **kwargs: payload)

    result = lookup_question_video("Q001", "https://cms.example/question", "token")

    assert result.status == "found"
    assert result.url == "https://example.com/q001.mp4"
    assert result.title == "题目一"


def test_lookup_question_video_found_without_url(monkeypatch):
    payload = {"data": {"question_uuid": "Q001", "body": {"content": "题目一"}, "video_data": []}}
    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", lambda *args, **kwargs: payload)
    result = lookup_question_video("Q001", "https://cms.example/question", "token")
    assert result.status == "missing_url"
    assert result.url == ""
    assert result.title == "题目一"


def test_lookup_question_video_not_found(monkeypatch):
    monkeypatch.setattr(
        "workspace_libs.cms.question._fetch_json", lambda *args, **kwargs: {"data": {}}
    )

    result = lookup_question_video("Q404", "https://cms.example/question", "token")

    assert result.status == "not_found"
    assert result.url == ""


def test_lookup_question_video_nonempty_error_payload_is_not_found(monkeypatch):
    payload = {"data": {"message": "question not found", "code": 404}}
    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", lambda *args, **kwargs: payload)

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
    monkeypatch.setattr("workspace_libs.cms.knowledge._fetch_json", lambda *args, **kwargs: payload)
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
    monkeypatch.setattr("workspace_libs.cms.knowledge._fetch_json", lambda *args, **kwargs: payload)
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
    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", lambda *args, **kwargs: payload)
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
    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", lambda *args, **kwargs: payload)
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

    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", fake_fetch)

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

    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", fake_fetch)

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
    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", lambda *args, **kwargs: payload)

    result = fetch_question_detail("Q001", "https://cms.example/question/detail", "token")

    assert result.question_id == "Q001"
    assert result.title == "1 + 1 = ?"
    assert result.normalized["stem"] == "1 + 1 = ?"
    assert result.normalized["options"] == [{"key": "A", "content": "2"}]
    assert result.payload == payload


# ---------------------------------------------------------------------------
# In-band error codes: the CMS signals auth/parameter failures as
# HTTP 200 + code != 0 + data: null — every endpoint must fail on them
# instead of parsing the payload as an empty result.
# ---------------------------------------------------------------------------


def test_fetch_question_detail_in_band_auth_code_raises(monkeypatch):
    payload = {"code": 10015, "message": "JWT验证失败", "data": None}
    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", lambda *a, **k: payload)

    with pytest.raises(CmsClientError, match="code=10015") as excinfo:
        fetch_question_detail("Q001", "https://cms.example/question/detail", "token")

    assert excinfo.value.auth_failure is True


def test_lookup_question_video_in_band_auth_code_raises(monkeypatch):
    payload = {"code": 10015, "message": "JWT验证失败", "data": None}
    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", lambda *a, **k: payload)

    with pytest.raises(CmsClientError, match="code=10015") as excinfo:
        lookup_question_video("Q001", "https://cms.example/question", "token")

    assert excinfo.value.auth_failure is True


def test_list_questions_by_knowledge_in_band_auth_code_raises(monkeypatch):
    payload = {"code": 10015, "message": "JWT验证失败", "data": None}
    monkeypatch.setattr("workspace_libs.cms.question._fetch_json", lambda *a, **k: payload)

    with pytest.raises(CmsClientError, match="code=10015") as excinfo:
        list_questions_by_knowledge("K001", "https://cms.example/question/list", "token")

    assert excinfo.value.auth_failure is True


def test_lookup_knowledge_video_in_band_auth_code_raises(monkeypatch):
    payload = {"code": 10015, "message": "JWT验证失败", "data": None}
    monkeypatch.setattr("workspace_libs.cms.knowledge._fetch_json", lambda *a, **k: payload)

    with pytest.raises(CmsClientError, match="code=10015") as excinfo:
        lookup_knowledge_video("K001", "https://cms.example/knowledge", "token")

    assert excinfo.value.auth_failure is True


def test_in_band_non_auth_code_is_not_auth_failure(monkeypatch):
    # Parameter errors fail the call but must not invalidate the token.
    payload = {"code": 40001, "message": "参数错误", "data": None}
    monkeypatch.setattr("workspace_libs.cms.knowledge._fetch_json", lambda *a, **k: payload)

    with pytest.raises(CmsClientError, match="code=40001") as excinfo:
        lookup_knowledge_video("K001", "https://cms.example/knowledge", "token")

    assert excinfo.value.auth_failure is False


class _HttpErrorResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        raise requests.HTTPError(f"{self.status_code} Error", response=self)

    def json(self) -> dict:
        return {}


@pytest.mark.parametrize("status_code", [401, 403])
def test_fetch_json_http_auth_status_flags_auth_failure(monkeypatch, status_code):
    monkeypatch.setattr(cms_client.requests, "get", lambda *a, **k: _HttpErrorResponse(status_code))

    with pytest.raises(CmsClientError, match="CMS request failed") as excinfo:
        cms_client._fetch_json("https://cms.example/x", {}, "token")

    assert excinfo.value.auth_failure is True


def test_fetch_json_http_500_is_not_auth_failure(monkeypatch):
    monkeypatch.setattr(cms_client.requests, "get", lambda *a, **k: _HttpErrorResponse(500))

    with pytest.raises(CmsClientError, match="CMS request failed") as excinfo:
        cms_client._fetch_json("https://cms.example/x", {}, "token")

    assert excinfo.value.auth_failure is False


def test_fetch_json_transport_error_is_not_auth_failure(monkeypatch):
    def _down(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(cms_client.requests, "get", _down)

    with pytest.raises(CmsClientError, match="CMS request failed") as excinfo:
        cms_client._fetch_json("https://cms.example/x", {}, "token")

    assert excinfo.value.auth_failure is False
