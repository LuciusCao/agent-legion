"""user_login_jwt adapter: two-step login flow, payload shape, failure hygiene."""

from __future__ import annotations

import pytest
import requests

from server.app.services.connection_adapter_user_login import (
    USER_LOGIN_JWT_ADAPTER as ADAPTER,
)
from server.app.services.connection_adapters import (
    ConnectionAdapterError,
    get_adapter,
    list_adapter_types,
)

_CONFIG = {
    "app_id": 78002100,
    "login_url": "http://user-center.internal/user/user/login",
    "auth_url": "https://addons.example/common/v1/auth",
    "client_params": '{"source":"SPAD"}',
}
_SECRETS = {"uname": "alice", "password": "s3cret"}


class _FakeResponse:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _FakeSession:
    """Records posts; serves scripted responses per URL substring."""

    def __init__(self, scripted: dict[str, _FakeResponse]) -> None:
        self._scripted = scripted
        self.trust_env = True
        self.posts: list[dict] = []

    def post(self, url: str, **kwargs) -> _FakeResponse:
        self.posts.append({"url": url, **kwargs})
        for needle, response in self._scripted.items():
            if needle in url:
                return response
        raise AssertionError(f"unexpected POST {url}")


def _mock_session(
    monkeypatch: pytest.MonkeyPatch, scripted: dict[str, _FakeResponse]
) -> _FakeSession:
    session = _FakeSession(scripted)
    monkeypatch.setattr(requests, "Session", lambda: session)
    return session


def _ok_script(token: str = "header.eyJleHAiOjQxMDI0NDQ4MDB9.sig") -> dict[str, _FakeResponse]:
    # exp=4102444800 (2100-01-01) keeps jwt_expires_at deterministic.
    return {
        "login": _FakeResponse({"code": 200, "data": {"user_token": "ut-1"}}),
        "auth": _FakeResponse({"code": 0, "data": {"token": token}}),
    }


@pytest.mark.no_db
def test_registered_and_listed() -> None:
    assert get_adapter("user_login_jwt") is ADAPTER
    types = {t["type"]: t for t in list_adapter_types()}
    meta = types["user_login_jwt"]
    assert meta["required_config_keys"] == ["app_id", "login_url", "auth_url"]
    assert meta["secret_keys"] == ["uname", "password"]


@pytest.mark.no_db
def test_two_step_flow_success(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _mock_session(monkeypatch, _ok_script())
    acquired = ADAPTER.authenticate(dict(_CONFIG), dict(_SECRETS))
    assert acquired.token.startswith("header.")
    assert acquired.expires_at is not None and acquired.expires_at.year == 2100

    assert session.trust_env is False  # internal hosts must bypass local proxies
    login, exchange = session.posts
    assert login["json"] == {
        "app_id": 78002100,
        "account_type": 1,
        "uname": "alice",
        "password": "s3cret",
        "client_params": '{"source":"SPAD"}',
    }
    assert login["allow_redirects"] is False and login["timeout"] == 10
    assert exchange["json"] == {"user_token": "ut-1", "app_id": 78002100}
    assert exchange["allow_redirects"] is False


@pytest.mark.no_db
def test_opaque_token_falls_back_to_default_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_session(monkeypatch, _ok_script(token="opaque-token"))
    acquired = ADAPTER.authenticate(dict(_CONFIG), dict(_SECRETS))
    assert acquired.token == "opaque-token"
    assert acquired.expires_at is not None and acquired.expires_at.year < 2100


@pytest.mark.no_db
@pytest.mark.parametrize("dropped", ["app_id", "login_url", "auth_url"])
def test_missing_config_keys(dropped: str) -> None:
    config = {k: v for k, v in _CONFIG.items() if k != dropped}
    with pytest.raises(ConnectionAdapterError, match="缺少配置"):
        ADAPTER.authenticate(config, dict(_SECRETS))


@pytest.mark.no_db
@pytest.mark.parametrize("dropped", ["uname", "password"])
def test_missing_secrets(dropped: str) -> None:
    secrets = {k: v for k, v in _SECRETS.items() if k != dropped}
    with pytest.raises(ConnectionAdapterError, match="缺少配置"):
        ADAPTER.authenticate(dict(_CONFIG), secrets)


@pytest.mark.no_db
def test_login_failure_does_not_echo_body(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"code": -9980440, "message": "biz gate rejected", "echo": {"password": "s3cret"}}
    _mock_session(monkeypatch, {"login": _FakeResponse(body)})
    with pytest.raises(ConnectionAdapterError) as excinfo:
        ADAPTER.authenticate(dict(_CONFIG), dict(_SECRETS))
    text = str(excinfo.value)
    assert "-9980440" in text and "biz gate" in text
    assert "s3cret" not in text and "echo" not in text


@pytest.mark.no_db
def test_exchange_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _ok_script()
    script["auth"] = _FakeResponse({"code": 10001, "message": "bad user_token"})
    _mock_session(monkeypatch, script)
    with pytest.raises(ConnectionAdapterError, match="换 token"):
        ADAPTER.authenticate(dict(_CONFIG), dict(_SECRETS))


@pytest.mark.no_db
def test_missing_token_field_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _ok_script()
    script["auth"] = _FakeResponse({"code": 0, "data": {}})
    _mock_session(monkeypatch, script)
    with pytest.raises(ConnectionAdapterError, match="换 token"):
        ADAPTER.authenticate(dict(_CONFIG), dict(_SECRETS))


@pytest.mark.no_db
def test_login_resolve_ip_rewrites_dial_and_preserves_host(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _mock_session(monkeypatch, _ok_script())
    config = {**_CONFIG, "login_resolve_ip": "10.0.0.8"}
    ADAPTER.authenticate(config, dict(_SECRETS))
    login = session.posts[0]
    assert login["url"].startswith("http://10.0.0.8/")
    assert login["headers"]["Host"] == "user-center.internal"
