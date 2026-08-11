"""bearer_probe status classification: only 2xx counts as connected."""

from __future__ import annotations

import pytest
import requests

from server.app.services.connection_adapters import (
    ConnectionAdapterError,
    bearer_probe,
)

_CONFIG = {"probe_url": "http://cms.example/probe"}


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _mock_get(monkeypatch, status_code: int) -> dict:
    seen: dict = {}

    def fake_get(*args, **kwargs):
        seen.update(kwargs)
        return _FakeResponse(status_code)

    monkeypatch.setattr(requests, "get", fake_get)
    return seen


@pytest.mark.no_db
def test_probe_2xx_reports_success(monkeypatch) -> None:
    _mock_get(monkeypatch, 200)
    assert "连接成功" in bearer_probe(_CONFIG, "tok")


@pytest.mark.no_db
@pytest.mark.parametrize("status_code", [401, 403])
def test_probe_auth_failure(monkeypatch, status_code: int) -> None:
    _mock_get(monkeypatch, status_code)
    with pytest.raises(ConnectionAdapterError, match="鉴权失败"):
        bearer_probe(_CONFIG, "tok")


@pytest.mark.no_db
@pytest.mark.parametrize("status_code", [301, 404, 500, 502])
def test_probe_unexpected_status_is_not_success(monkeypatch, status_code: int) -> None:
    """5xx/404/3xx mean the endpoint answered but is not serving the probed
    resource: reachable, not "connected"."""
    _mock_get(monkeypatch, status_code)
    with pytest.raises(ConnectionAdapterError, match="端点响应异常"):
        bearer_probe(_CONFIG, "tok")


@pytest.mark.no_db
def test_probe_does_not_follow_redirects(monkeypatch) -> None:
    seen = _mock_get(monkeypatch, 200)
    bearer_probe(_CONFIG, "tok")
    assert seen["allow_redirects"] is False
