"""Unit tests for scripts/stress/_stress_auth.py (deterministic stress admin session)."""

from __future__ import annotations

from unittest import mock

import pytest

from scripts.stress import _stress_auth


def _response(payload: dict, status_code: int = 200) -> mock.Mock:
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_ensure_admin_session_bootstraps_when_available():
    session = mock.Mock()
    session.get.return_value = _response({"available": True})
    session.post.return_value = _response({"username": "stress-admin"})

    with mock.patch.object(_stress_auth.requests, "Session", return_value=session):
        result = _stress_auth.ensure_admin_session("http://127.0.0.1:8000/")

    assert result is session
    session.headers.update.assert_called_once_with({"x-agent-legion-request": "1"})
    session.get.assert_called_once_with(
        "http://127.0.0.1:8000/api/auth/bootstrap", timeout=mock.ANY
    )
    (url,), kwargs = session.post.call_args
    assert url == "http://127.0.0.1:8000/api/auth/bootstrap"
    assert kwargs["json"]["username"] == "stress-admin"
    assert "display_name" in kwargs["json"]


def test_ensure_admin_session_logs_in_when_bootstrap_unavailable():
    session = mock.Mock()
    session.get.return_value = _response({"available": False})
    session.post.return_value = _response({"username": "stress-admin"})

    with mock.patch.object(_stress_auth.requests, "Session", return_value=session):
        _stress_auth.ensure_admin_session("http://127.0.0.1:8000")

    (url,), kwargs = session.post.call_args
    assert url == "http://127.0.0.1:8000/api/auth/login"
    assert kwargs["json"] == {
        "username": "stress-admin",
        "password": "stress-admin-password-1",
    }


def test_ensure_admin_session_raises_on_auth_failure():
    session = mock.Mock()
    session.get.return_value = _response({"available": False})
    failure = _response({}, status_code=401)
    failure.raise_for_status.side_effect = RuntimeError("401")
    session.post.return_value = failure

    with (
        mock.patch.object(_stress_auth.requests, "Session", return_value=session),
        pytest.raises(RuntimeError, match="401"),
    ):
        _stress_auth.ensure_admin_session("http://127.0.0.1:8000")


def test_session_cookie_extracts_value_and_defaults_empty():
    session = mock.Mock()
    session.cookies.get.return_value = "cookie-value"
    assert _stress_auth.session_cookie(session) == "cookie-value"
    session.cookies.get.assert_called_once_with(_stress_auth.SESSION_COOKIE_NAME)

    session.cookies.get.return_value = None
    assert _stress_auth.session_cookie(session) == ""
