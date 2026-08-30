"""X-Request-Id middleware: generation, pass-through, slow-request logging.

Issue #273. The middleware itself is exercised on standalone FastAPI apps
(same pattern as the gzip middleware tests in tests/test_main.py); one
shared-client test verifies the real app wiring emits the header. No
database is touched by the standalone tests.
"""

from __future__ import annotations

import asyncio
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.app.http_middleware import add_http_middleware
from server.app.http_request_id import (
    RequestIdMiddleware,
    current_request_id,
    slow_request_threshold_ms,
)

_HEX_ID = re.compile(r"^[0-9a-f]{16}$")


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def read_item(item_id: int) -> dict[str, object]:
        return {"item_id": item_id, "request_id": current_request_id()}

    @app.get("/workspaces/{workspace_id}/jobs")
    async def slow_route(workspace_id: str) -> dict[str, str]:
        await asyncio.sleep(0.03)
        return {"workspace_id": workspace_id}

    app.add_middleware(RequestIdMiddleware)
    return app


@pytest.mark.no_db
def test_missing_header_generates_hex_request_id() -> None:
    with TestClient(_build_app()) as client:
        response = client.get("/items/42")
    assert response.status_code == 200
    assert _HEX_ID.match(response.headers["x-request-id"])


@pytest.mark.no_db
def test_valid_upstream_id_passes_through() -> None:
    with TestClient(_build_app()) as client:
        response = client.get("/items/1", headers={"X-Request-Id": "lb-trace_42-9"})
    assert response.headers["x-request-id"] == "lb-trace_42-9"


@pytest.mark.no_db
def test_illegal_upstream_id_is_regenerated() -> None:
    """Log-injection payloads must never be echoed back verbatim."""
    with TestClient(_build_app()) as client:
        for bad in (
            "evil id\nX-Forged: 1",  # header injection
            "\x1b[31mred\x1b[0m",  # ANSI escape
            "id%20with%20space",  # URL-encoded spaces (decoded before us)
            "x" * 200,  # oversized
        ):
            response = client.get("/items/1", headers={"X-Request-Id": bad})
            assert _HEX_ID.match(response.headers["x-request-id"]), bad


@pytest.mark.no_db
def test_contextvar_available_inside_handler_and_reset_after() -> None:
    app = _build_app()
    seen: dict[str, str | None] = {}

    async def outer(scope, receive, send):
        await app(scope, receive, send)
        # The middleware must reset the contextvar once the request is done.
        seen["after"] = current_request_id()

    with TestClient(outer) as client:
        response = client.get("/items/7")
    body = response.json()
    assert body["request_id"] == response.headers["x-request-id"]
    assert seen["after"] is None


@pytest.mark.no_db
def test_consecutive_requests_get_distinct_ids() -> None:
    with TestClient(_build_app()) as client:
        first = client.get("/items/1").headers["x-request-id"]
        second = client.get("/items/1").headers["x-request-id"]
    assert first != second


@pytest.mark.no_db
def test_slow_request_logs_route_template_not_raw_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AGENT_LEGION_SLOW_REQUEST_MS", "1")
    with (
        caplog.at_level(logging.WARNING, logger="server.app.http_request_id"),
        TestClient(_build_app()) as client,
    ):
        response = client.get("/workspaces/ws-demo/jobs")
    assert response.status_code == 200
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    record = warnings[0]
    assert "path=/workspaces/{workspace_id}/jobs" in record.getMessage()
    assert "method=GET" in record.getMessage()
    assert "status=200" in record.getMessage()
    assert f"request_id={response.headers['x-request-id']}" in record.getMessage()
    assert "duration_ms=" in record.getMessage()


@pytest.mark.no_db
def test_fast_request_is_not_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AGENT_LEGION_SLOW_REQUEST_MS", "600000")
    with (
        caplog.at_level(logging.WARNING, logger="server.app.http_request_id"),
        TestClient(_build_app()) as client,
    ):
        assert client.get("/items/42").status_code == 200
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


@pytest.mark.no_db
def test_slow_request_threshold_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_LEGION_SLOW_REQUEST_MS", raising=False)
    assert slow_request_threshold_ms() == 1000.0
    monkeypatch.setenv("AGENT_LEGION_SLOW_REQUEST_MS", "250.5")
    assert slow_request_threshold_ms() == 250.5
    monkeypatch.setenv("AGENT_LEGION_SLOW_REQUEST_MS", "not-a-number")
    assert slow_request_threshold_ms() == 1000.0


@pytest.mark.no_db
def test_add_http_middleware_wires_request_id(tmp_path) -> None:
    """The mounting point used by create_app installs the middleware."""
    from server.app.settings import load_settings

    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    add_http_middleware(app, load_settings(data_dir=tmp_path))
    with TestClient(app) as client:
        response = client.get("/ping")
    assert _HEX_ID.match(response.headers["x-request-id"])


def test_real_app_responses_carry_request_id(anon_client) -> None:
    response = anon_client.get("/api/health")
    assert response.status_code == 200
    assert _HEX_ID.match(response.headers["x-request-id"])
