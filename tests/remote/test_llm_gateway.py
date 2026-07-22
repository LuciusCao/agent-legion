from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts.remote import llm_gateway
from scripts.remote.llm_gateway_config import load_pi_provider


class FakeUpstreamResponse:
    def __init__(
        self,
        status_code: int = 200,
        chunks=(),
        content_type="text/event-stream",
        stream_error: Exception | None = None,
    ):
        self.status_code = status_code
        self._chunks = chunks
        self._stream_error = stream_error
        self.headers = {"content-type": content_type}
        self.closed = False

    def iter_content(self, chunk_size: int = 8192):
        yield from self._chunks
        if self._stream_error is not None:
            raise self._stream_error

    def close(self):
        self.closed = True


def test_load_pi_provider_strips_openai_v1_suffix(tmp_path: Path):
    models_json = tmp_path / "models.json"
    models_json.write_text(
        json.dumps(
            {
                "providers": {
                    "gateway": {
                        "baseUrl": "https://llm.example.com/llmaiplatform/v1/",
                        "apiKey": "secret",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_pi_provider(models_json, "gateway") == (
        "https://llm.example.com/llmaiplatform",
        "secret",
    )


def test_load_pi_provider_reports_missing_provider_without_leaking_document(tmp_path: Path):
    models_json = tmp_path / "models.json"
    models_json.write_text('{"providers":{"other":{"apiKey":"secret"}}}', encoding="utf-8")

    with pytest.raises(ValueError, match="cannot load provider 'gateway'") as exc_info:
        load_pi_provider(models_json, "gateway")
    assert "secret" not in str(exc_info.value)


def test_proxy_forwards_body_and_streams(monkeypatch, caplog):
    captured = {}
    fake = FakeUpstreamResponse(chunks=[b"data: chunk1\n\n", b"data: chunk2\n\n"])

    def fake_post(url, data=None, headers=None, stream=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        captured["stream"] = stream
        return fake

    monkeypatch.setattr(llm_gateway.requests, "post", fake_post)
    app = llm_gateway.create_gateway_app("https://llm.example.com/", "super-secret-key")
    client = TestClient(app)

    body = {
        "model": "your-model-b",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    resp = client.post("/v1/chat/completions", json=body)

    assert resp.status_code == 200
    assert resp.content == b"data: chunk1\n\ndata: chunk2\n\n"
    assert captured["url"] == "https://llm.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer super-secret-key"
    assert json.loads(captured["data"]) == body
    assert captured["stream"] is True
    assert fake.closed is True
    assert "super-secret-key" not in caplog.text
    assert "super-secret-key" not in resp.text


def test_proxy_passthrough_upstream_error(monkeypatch):
    fake = FakeUpstreamResponse(
        status_code=429, chunks=[b'{"error":"rate limited"}'], content_type="application/json"
    )
    monkeypatch.setattr(llm_gateway.requests, "post", lambda *a, **k: fake)
    app = llm_gateway.create_gateway_app("https://llm.example.com", "k")
    resp = TestClient(app).post("/v1/chat/completions", json={})
    assert resp.status_code == 429
    assert b"rate limited" in resp.content


def test_proxy_requires_gateway_token_when_configured(monkeypatch):
    fake = FakeUpstreamResponse(chunks=[b"data: ok\n\n"])
    monkeypatch.setattr(llm_gateway.requests, "post", lambda *a, **k: fake)
    app = llm_gateway.create_gateway_app("https://llm.example.com", "k", gateway_token="tok123")
    client = TestClient(app)

    assert client.post("/v1/chat/completions", json={}).status_code == 401
    resp = client.post("/v1/chat/completions", json={}, headers={"X-Gateway-Token": "wrong"})
    assert resp.status_code == 401


def test_proxy_accepts_gateway_token_via_header_or_bearer(monkeypatch):
    fake = FakeUpstreamResponse(chunks=[b"data: ok\n\n"])
    monkeypatch.setattr(llm_gateway.requests, "post", lambda *a, **k: fake)
    app = llm_gateway.create_gateway_app("https://llm.example.com", "k", gateway_token="tok123")
    client = TestClient(app)

    resp = client.post("/v1/chat/completions", json={}, headers={"X-Gateway-Token": "tok123"})
    assert resp.status_code == 200
    resp = client.post("/v1/chat/completions", json={}, headers={"Authorization": "Bearer tok123"})
    assert resp.status_code == 200


def test_proxy_rejects_path_traversal(monkeypatch):
    called = []

    def fake_post(url, **kwargs):
        called.append(url)
        return FakeUpstreamResponse()

    monkeypatch.setattr(llm_gateway.requests, "post", fake_post)
    app = llm_gateway.create_gateway_app("https://llm.example.com", "k")
    client = TestClient(app)
    # httpx normalizes literal ".." away client-side; percent-encoded dots
    # survive to the server and are decoded into the path parameter.
    assert client.post("/v1/%2E%2E/admin", json={}).status_code == 400
    assert client.post("/v1/foo/%2E%2E/%2E%2E/admin", json={}).status_code == 400
    assert called == []


def test_proxy_upstream_unreachable(monkeypatch):
    def boom(*a, **k):
        raise llm_gateway.requests.ConnectionError("no route")

    monkeypatch.setattr(llm_gateway.requests, "post", boom)
    app = llm_gateway.create_gateway_app("https://llm.example.com", "k")
    resp = TestClient(app).post("/v1/chat/completions", json={})
    assert resp.status_code == 502


def test_stream_tolerates_chunk_truncation_after_sse_done():
    fake = FakeUpstreamResponse(
        chunks=[b'data: {"delta":"pong"}\n\n', b"data: [DO", b"NE]\n\n"],
        stream_error=llm_gateway.requests.exceptions.ChunkedEncodingError(
            "Response ended prematurely"
        ),
    )

    assert b"".join(llm_gateway._stream_upstream(fake)) == (
        b'data: {"delta":"pong"}\n\ndata: [DONE]\n\n'
    )
    assert fake.closed is True


def test_stream_preserves_chunk_truncation_before_sse_done():
    fake = FakeUpstreamResponse(
        chunks=[b'data: {"delta":"partial"}\n\n'],
        stream_error=llm_gateway.requests.exceptions.ChunkedEncodingError(
            "Response ended prematurely"
        ),
    )

    with pytest.raises(llm_gateway.requests.exceptions.ChunkedEncodingError):
        b"".join(llm_gateway._stream_upstream(fake))
    assert fake.closed is True
