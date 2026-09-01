"""hmac_token adapter: signature exchange, expiry resolution, cache integration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
import requests
from cryptography.fernet import Fernet

from server.app.services.connection_adapter_hmac import HMAC_TOKEN_ADAPTER
from server.app.services.connection_adapters import (
    ConnectionAdapterError,
    get_adapter,
    list_adapter_types,
)
from server.app.services.connection_tokens import ConnectionTokenService
from server.app.services.connections import ConnectionService

_CONFIG = {
    "app_id": "app-1",
    "token_url": "http://auth.example/token",
    "probe_url": "http://api.example/ping",
}
_SECRETS = {"secret": "s3cret"}


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


@pytest.fixture
def services(job_db, settings, vault_key):
    connections = ConnectionService(job_db.dsn_identity, settings.config)
    tokens = ConnectionTokenService(job_db.dsn_identity, settings.config)
    return connections, tokens


def _jwt(exp: int) -> str:
    def seg(payload: dict) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg({'exp': exp})}.sig"


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = {} if payload is None else payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> object:
        return self._payload


def _mock_post(monkeypatch, response: _FakeResponse | None = None, exc: Exception | None = None):
    calls: list[dict] = []

    def fake_post(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(requests, "post", fake_post)
    return calls


def _expected_sign(secret: str, app_id: str, timestamp: str, nonce: str) -> str:
    msg = app_id + timestamp + nonce
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


@pytest.mark.no_db
def test_registered_as_lazy_platform_builtin() -> None:
    assert get_adapter("hmac_token") is HMAC_TOKEN_ADAPTER
    view = {t["type"]: t for t in list_adapter_types()}["hmac_token"]
    assert view["required_config_keys"] == ["app_id", "token_url"]
    assert view["secret_keys"] == ["secret"]


@pytest.mark.no_db
def test_authenticate_signs_and_exchanges_for_jwt(monkeypatch) -> None:
    exp = int(time.time()) + 3600
    calls = _mock_post(monkeypatch, _FakeResponse(200, {"token": _jwt(exp)}))

    acquired = HMAC_TOKEN_ADAPTER.authenticate(_CONFIG, _SECRETS)

    assert acquired.token == _jwt(exp)
    assert acquired.expires_at is not None and int(acquired.expires_at.timestamp()) == exp
    call = calls[0]
    assert call["args"][0] == "http://auth.example/token"
    assert call["kwargs"]["timeout"] == 10
    # The payload carries the plaintext secret: redirects must not be followed.
    assert call["kwargs"]["allow_redirects"] is False
    payload = call["kwargs"]["json"]
    assert payload["app_id"] == "app-1"
    assert payload["secret"] == "s3cret"
    assert abs(int(payload["timestamp"]) - int(time.time())) <= 2
    assert len(payload["nonce"]) == 32  # random per request by default
    assert payload["sign"] == _expected_sign(
        "s3cret", "app-1", payload["timestamp"], payload["nonce"]
    )


@pytest.mark.no_db
def test_authenticate_random_nonce_unless_config_pins_it(monkeypatch) -> None:
    calls = _mock_post(monkeypatch, _FakeResponse(200, {"token": "tok"}))
    HMAC_TOKEN_ADAPTER.authenticate(_CONFIG, _SECRETS)
    HMAC_TOKEN_ADAPTER.authenticate(_CONFIG, _SECRETS)
    assert calls[0]["kwargs"]["json"]["nonce"] != calls[1]["kwargs"]["json"]["nonce"]

    calls.clear()
    HMAC_TOKEN_ADAPTER.authenticate({**_CONFIG, "nonce": "fixed"}, _SECRETS)
    payload = calls[0]["kwargs"]["json"]
    assert payload["nonce"] == "fixed"
    assert payload["sign"] == _expected_sign("s3cret", "app-1", payload["timestamp"], "fixed")


@pytest.mark.no_db
@pytest.mark.parametrize(
    "payload, expected_seconds",
    [
        ({"token": "tok", "expires_in": 600}, 600),
        ({"data": {"token": "tok", "expires_in": "300"}}, 300),
    ],
)
def test_authenticate_expires_in_fallback(monkeypatch, payload, expected_seconds) -> None:
    _mock_post(monkeypatch, _FakeResponse(200, payload))
    before = datetime.now(UTC)
    acquired = HMAC_TOKEN_ADAPTER.authenticate(_CONFIG, _SECRETS)
    assert acquired.token == "tok"
    delta = acquired.expires_at - before
    assert timedelta(seconds=expected_seconds - 5) < delta < timedelta(seconds=expected_seconds + 5)


@pytest.mark.no_db
def test_authenticate_opaque_token_gets_conservative_default_ttl(monkeypatch) -> None:
    _mock_post(monkeypatch, _FakeResponse(200, {"token": "opaque"}))
    before = datetime.now(UTC)
    acquired = HMAC_TOKEN_ADAPTER.authenticate(_CONFIG, _SECRETS)
    delta = acquired.expires_at - before
    assert timedelta(minutes=29) < delta < timedelta(minutes=30, seconds=5)


@pytest.mark.no_db
@pytest.mark.parametrize(
    "config, secrets, missing",
    [
        ({**_CONFIG, "app_id": ""}, _SECRETS, "app_id"),
        ({**_CONFIG, "token_url": " "}, _SECRETS, "token_url"),
        (_CONFIG, {"secret": ""}, "secret"),
        ({}, {}, "app_id, token_url, secret"),
    ],
)
def test_authenticate_missing_config_or_secret(config, secrets, missing) -> None:
    with pytest.raises(ConnectionAdapterError, match=f"缺少配置: {missing}"):
        HMAC_TOKEN_ADAPTER.authenticate(config, secrets)


@pytest.mark.no_db
@pytest.mark.parametrize("status_code", [400, 401, 500, 503])
def test_authenticate_upstream_error_status(monkeypatch, status_code) -> None:
    _mock_post(monkeypatch, _FakeResponse(status_code, {"error": "boom"}))
    with pytest.raises(ConnectionAdapterError, match="token 请求失败"):
        HMAC_TOKEN_ADAPTER.authenticate(_CONFIG, _SECRETS)


@pytest.mark.no_db
def test_authenticate_timeout(monkeypatch) -> None:
    _mock_post(monkeypatch, exc=requests.Timeout("timed out"))
    with pytest.raises(ConnectionAdapterError, match="token 请求失败"):
        HMAC_TOKEN_ADAPTER.authenticate(_CONFIG, _SECRETS)


@pytest.mark.no_db
def test_authenticate_response_without_token_never_echoes_body(monkeypatch) -> None:
    _mock_post(monkeypatch, _FakeResponse(200, {"echo": "s3cret", "msg": "no token here"}))
    with pytest.raises(ConnectionAdapterError, match="响应缺少 token 字段") as exc_info:
        HMAC_TOKEN_ADAPTER.authenticate(_CONFIG, _SECRETS)
    assert "s3cret" not in str(exc_info.value)
    assert "no token here" not in str(exc_info.value)


@pytest.mark.no_db
def test_probe_reuses_bearer_probe(monkeypatch) -> None:
    _mock_post(monkeypatch, _FakeResponse(200, {"token": "tok"}))
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(200))
    assert "连接成功" in HMAC_TOKEN_ADAPTER.probe(_CONFIG, _SECRETS)


def test_get_token_caches_valid_hmac_token(services, monkeypatch) -> None:
    """A valid cached token is served without a second credential exchange."""
    exp = int(time.time()) + 3600
    calls = _mock_post(monkeypatch, _FakeResponse(200, {"token": _jwt(exp)}))
    connections, tokens = services
    connections.create("c1", "hmac_token", "", {**_CONFIG, "secret": "s3cret"})

    assert tokens.get_token("c1") == _jwt(exp)
    assert tokens.get_token("c1") == _jwt(exp)
    assert len(calls) == 1


def test_get_token_renew_after_expiry(services, monkeypatch) -> None:
    """An expired cached token triggers a fresh HMAC exchange."""
    expired = _jwt(int(time.time()) - 10)
    fresh = _jwt(int(time.time()) + 3600)
    responses = iter([_FakeResponse(200, {"token": expired}), _FakeResponse(200, {"token": fresh})])
    calls: list[dict] = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(requests, "post", fake_post)
    connections, tokens = services
    connections.create("c1", "hmac_token", "", {**_CONFIG, "secret": "s3cret"})

    assert tokens.get_token("c1") == expired
    # The first acquisition cached an already-expired token, so the next read
    # re-exchanges instead of serving the stale entry.
    assert tokens.get_token("c1") == fresh
    assert len(calls) == 2
