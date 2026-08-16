"""Unit tests for workspace_libs/http_client.py (framework HTTP primitives)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from workspace_libs.http_client import (
    HttpServiceError,
    bearer_headers,
    check_in_band_error,
    config_token,
    download_file,
    fetch_json,
    require_configured_url,
    validate_download_url,
)

pytestmark = pytest.mark.no_db


class NodeServiceError(HttpServiceError):
    """Stand-in for a node's business error subclass."""


def _response(status: int = 200, payload: Any = None, json_error: bool = False) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    if json_error:
        resp.json.side_effect = ValueError("bad json")
    else:
        resp.json.return_value = payload if payload is not None else {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} error", response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# headers / token / url guard


def test_bearer_headers() -> None:
    assert bearer_headers(None) == {"Accept": "*/*"}
    assert bearer_headers("") == {"Accept": "*/*"}
    # Verbatim port of the retired _build_headers: a whitespace-only token is
    # truthy and yields a bare "Bearer " header (callers strip via
    # config_token first, so this never fires in practice).
    assert bearer_headers("  ")["Authorization"] == "Bearer "
    assert bearer_headers("tok")["Authorization"] == "Bearer tok"
    assert bearer_headers("bearer tok")["Authorization"] == "bearer tok"


def test_config_token_reads_merged_config() -> None:
    assert config_token({"token": " t "}) == "t"
    assert config_token({}) is None
    assert config_token({"token": ""}) is None


def test_require_configured_url() -> None:
    assert require_configured_url(" https://x ", service="SVC", resource="r") == "https://x"
    with pytest.raises(NodeServiceError) as excinfo:
        require_configured_url("", service="SVC", resource="list", error_type=NodeServiceError)
    assert type(excinfo.value).__name__ == "NodeServiceError"
    assert "SVC list URL is not configured" in str(excinfo.value)
    assert excinfo.value.auth_failure is False


# ---------------------------------------------------------------------------
# fetch_json


def test_fetch_json_success(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(
        requests,
        "get",
        lambda url, **kw: seen.update(url=url, **kw) or _response(200, {"code": 0}),
    )
    payload = fetch_json("https://x/api", {"a": 1}, "tok", service="SVC")
    assert payload == {"code": 0}
    assert seen["headers"]["Authorization"] == "Bearer tok"
    assert seen["timeout"] == 15


@pytest.mark.parametrize("status,auth", [(401, True), (403, True), (500, False)])
def test_fetch_json_http_error_classification(
    monkeypatch: pytest.MonkeyPatch, status: int, auth: bool
) -> None:
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _response(status))
    with pytest.raises(NodeServiceError) as excinfo:
        fetch_json("https://x", {}, None, service="SVC", error_type=NodeServiceError)
    assert excinfo.value.auth_failure is auth
    assert str(excinfo.value).startswith(f"SVC request failed: {status} error")


def test_fetch_json_transport_and_parse_errors_not_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: Any, **kw: Any) -> None:
        raise requests.ConnectionError("dns")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(HttpServiceError, match="SVC request failed") as excinfo:
        fetch_json("https://x", {}, None, service="SVC")
    assert excinfo.value.auth_failure is False

    monkeypatch.setattr(requests, "get", lambda *a, **kw: _response(200, json_error=True))
    with pytest.raises(HttpServiceError, match="SVC request failed"):
        fetch_json("https://x", {}, None, service="SVC")


# ---------------------------------------------------------------------------
# check_in_band_error


@pytest.mark.parametrize("payload", [None, [], {}, {"code": 0}, {"code": "0"}, {"code": " 0 "}])
def test_in_band_success_payloads_pass(payload: Any) -> None:
    check_in_band_error(payload, "r", auth_codes=frozenset({10015}), service="SVC")


def test_in_band_error_raises_with_code_and_message() -> None:
    with pytest.raises(NodeServiceError) as excinfo:
        check_in_band_error(
            {"code": 40001, "message": "bad param"},
            "question_id=q1",
            auth_codes=frozenset({10015}),
            service="SVC",
            error_type=NodeServiceError,
        )
    assert str(excinfo.value) == "SVC 返回错误: code=40001 message=bad param (question_id=q1)"
    assert excinfo.value.auth_failure is False


def test_in_band_auth_code_flags_auth_failure() -> None:
    with pytest.raises(HttpServiceError) as excinfo:
        check_in_band_error(
            {"code": 10015, "message": "JWT验证失败"},
            "r",
            auth_codes=frozenset({10015}),
            service="SVC",
        )
    assert excinfo.value.auth_failure is True


def test_in_band_non_numeric_code_not_auth() -> None:
    with pytest.raises(HttpServiceError) as excinfo:
        check_in_band_error({"code": "abc"}, "r", auth_codes=frozenset({10015}), service="SVC")
    assert excinfo.value.auth_failure is False


# ---------------------------------------------------------------------------
# validate_download_url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://example.com/v.mp4",
        "http://localhost/v.mp4",
        "http://127.0.0.1/v.mp4",
        "http://10.0.0.1/v.mp4",
        "http://169.254.1.1/v.mp4",
        "http://0x7f000001/v.mp4",
        "http://2130706433/v.mp4",
        "https:///no-host",
    ],
)
def test_validate_download_url_rejects(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid URL"):
        validate_download_url(url)


@pytest.mark.parametrize("url", ["https://cdn.example.com/v.mp4", "http://8.8.8.8/v.mp4"])
def test_validate_download_url_accepts(url: str) -> None:
    validate_download_url(url)


# ---------------------------------------------------------------------------
# download_file


def _stream_response(chunks: list[bytes], content_type: str = "video/mp4") -> MagicMock:
    resp = MagicMock()
    resp.headers = {"content-type": content_type}
    resp.raise_for_status.return_value = None
    resp.iter_content.return_value = iter(chunks)
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_download_file_streams_to_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _stream_response([b"ab", b"", b"cd"]))
    out = tmp_path / "sub" / "source.mp4"
    download_file("https://cdn.example.com/v.mp4", out)
    assert out.read_bytes() == b"abcd"


def test_download_file_skips_existing_nonempty(tmp_path: Path) -> None:
    out = tmp_path / "source.mp4"
    out.write_bytes(b"done")
    # No monkeypatch of requests.get: any HTTP attempt would raise.
    download_file("https://cdn.example.com/v.mp4", out)
    assert out.read_bytes() == b"done"


def test_download_file_rejects_wrong_content_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _stream_response([b"x"], "text/html"))
    with pytest.raises(ValueError, match="Expected video content, got text/html"):
        download_file("https://cdn.example.com/v.mp4", tmp_path / "o.bin")


def test_download_file_cleans_partial_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    resp = _stream_response([b"partial"])

    def exploding_iter(chunk_size: int) -> Any:
        yield b"partial"
        raise requests.ConnectionError("reset")

    resp.iter_content = exploding_iter
    monkeypatch.setattr(requests, "get", lambda *a, **kw: resp)
    out = tmp_path / "o.bin"
    with pytest.raises(requests.ConnectionError):
        download_file("https://cdn.example.com/v.mp4", out)
    assert not out.exists()


def test_download_file_custom_content_type_prefixes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(requests, "get", lambda *a, **kw: _stream_response([b"x"], "audio/wav"))
    out = tmp_path / "a.wav"
    download_file(
        "https://cdn.example.com/a.wav",
        out,
        allowed_content_type_prefixes=("audio/",),
        expected="audio",
    )
    assert out.read_bytes() == b"x"
