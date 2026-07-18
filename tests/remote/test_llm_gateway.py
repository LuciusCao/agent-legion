from __future__ import annotations

import json

from fastapi.testclient import TestClient

from scripts.remote import llm_gateway


class FakeUpstreamResponse:
    def __init__(self, status_code: int = 200, chunks=(), content_type="text/event-stream"):
        self.status_code = status_code
        self._chunks = chunks
        self.headers = {"content-type": content_type}
        self.closed = False

    def iter_content(self, chunk_size: int = 8192):
        yield from self._chunks

    def close(self):
        self.closed = True


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


def test_proxy_upstream_unreachable(monkeypatch):
    def boom(*a, **k):
        raise llm_gateway.requests.ConnectionError("no route")

    monkeypatch.setattr(llm_gateway.requests, "post", boom)
    app = llm_gateway.create_gateway_app("https://llm.example.com", "k")
    resp = TestClient(app).post("/v1/chat/completions", json={})
    assert resp.status_code == 502
