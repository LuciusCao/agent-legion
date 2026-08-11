"""cms_hmac connection adapter: HMAC signature exchange and token parsing."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
import requests

from server.app.services.connection_adapters import ConnectionAdapterError
from workspace_libs.cms.adapters import CMS_HMAC_ADAPTER

pytestmark = pytest.mark.no_db

_CONFIG = {
    "app_id": "app",
    "nonce": "nonce",
    "token_url": "http://token.example/generate",
}
_SECRETS = {"secret": "secret"}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def _jwt(exp: int) -> str:
    def _segment(payload: dict) -> str:
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    return f"{_segment({'alg': 'none'})}.{_segment({'exp': exp})}.sig"


def test_adapter_metadata():
    assert CMS_HMAC_ADAPTER.type == "cms_hmac"
    assert CMS_HMAC_ADAPTER.required_config_keys == ("app_id", "nonce", "token_url")
    assert CMS_HMAC_ADAPTER.secret_keys == ("secret",)


@pytest.mark.parametrize("missing", ["app_id", "nonce", "token_url"])
def test_authenticate_rejects_missing_config_key(missing):
    config = {key: value for key, value in _CONFIG.items() if key != missing}
    with pytest.raises(ConnectionAdapterError, match=missing):
        CMS_HMAC_ADAPTER.authenticate(config, _SECRETS)


def test_authenticate_rejects_missing_secret():
    with pytest.raises(ConnectionAdapterError, match="secret"):
        CMS_HMAC_ADAPTER.authenticate(_CONFIG, {})


def test_authenticate_signs_app_id_timestamp_nonce(monkeypatch):
    calls = []
    monkeypatch.setattr("workspace_libs.cms.adapters.time.time", lambda: 1_700_000_000)

    def fake_post(url, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse({"token": "generated-token"})

    monkeypatch.setattr(requests, "post", fake_post)

    acquired = CMS_HMAC_ADAPTER.authenticate(_CONFIG, _SECRETS)

    assert acquired.token == "generated-token"
    expected_sign = hmac.new(b"secret", b"app1700000000nonce", hashlib.sha256).hexdigest()
    assert calls[0]["url"] == "http://token.example/generate"
    assert calls[0]["json"] == {
        "app_id": "app",
        "sign": expected_sign,
        "timestamp": "1700000000",
        "nonce": "nonce",
        "secret": "secret",
    }


def test_authenticate_reads_nested_data_token(monkeypatch):
    monkeypatch.setattr(
        requests, "post", lambda *args, **kwargs: FakeResponse({"data": {"token": "nested"}})
    )

    acquired = CMS_HMAC_ADAPTER.authenticate(_CONFIG, _SECRETS)

    assert acquired.token == "nested"


def test_authenticate_parses_jwt_exp(monkeypatch):
    token = _jwt(1_800_000_000)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FakeResponse({"token": token}))

    acquired = CMS_HMAC_ADAPTER.authenticate(_CONFIG, _SECRETS)

    assert acquired.expires_at == datetime.fromtimestamp(1_800_000_000, tz=UTC)


def test_authenticate_non_jwt_token_has_no_expiry(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FakeResponse({"token": "opaque"}))

    acquired = CMS_HMAC_ADAPTER.authenticate(_CONFIG, _SECRETS)

    assert acquired.expires_at is None


def test_authenticate_rejects_response_without_token(monkeypatch):
    monkeypatch.setattr(
        requests, "post", lambda *args, **kwargs: FakeResponse({"data": {"other": 1}})
    )

    with pytest.raises(ConnectionAdapterError, match="生成 token 失败"):
        CMS_HMAC_ADAPTER.authenticate(_CONFIG, _SECRETS)


def test_authenticate_wraps_request_failure(monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(ConnectionAdapterError, match="CMS token request failed"):
        CMS_HMAC_ADAPTER.authenticate(_CONFIG, _SECRETS)


def test_probe_without_probe_url_skips_connectivity_check(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FakeResponse({"token": "t"}))

    message = CMS_HMAC_ADAPTER.probe(_CONFIG, _SECRETS)

    assert "跳过连通性探测" in message
