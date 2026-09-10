"""CampaignBodyLimitMiddleware: pure-ASGI unit tests (PR #541 round-3 P1).

The HTTP-face integration (declared content-length 413 over the real app)
lives in tests/routes/test_campaigns_api_upload_preview.py; this file
drives the ASGI callable directly for the edges TestClient cannot reach:
chunked oversize bodies, ambiguous/duplicated content-length headers, path
scoping, and the live settings resolution of the ceiling. Pure static
(no_db, no app fixture).
"""

from __future__ import annotations

import asyncio

import pytest

from server.app.routes.campaign_body_limit import CampaignBodyLimitMiddleware

pytestmark = pytest.mark.no_db


class _Capture:
    def __init__(self) -> None:
        self.status: int | None = None
        self.body = b""

    async def __call__(self, message: dict) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body":
            self.body += message["body"]


def _settings(manifest_max_bytes: int):
    return type(
        "S",
        (),
        {
            "executor_runtime": type(
                "E",
                (),
                {"campaigns": type("C", (), {"manifest_max_bytes": manifest_max_bytes})()},
            )()
        },
    )()


class TestCampaignBodyLimitMiddleware:
    """纯 ASGI 驱动：分块超限、歧义 content-length、路径放行、动态上限。"""

    def test_chunked_body_over_limit_413(self):
        chunks = [b"a" * 700, b"b" * 700]

        async def receive() -> dict:
            if chunks:
                return {"type": "http.request", "body": chunks.pop(0), "more_body": True}
            return {"type": "http.request", "body": b"", "more_body": False}

        async def app(scope, receive, send):  # the app never finishes its read
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break

        capture = _Capture()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/workspaces/ws/campaigns",
            "headers": [],
        }
        middleware = CampaignBodyLimitMiddleware(app, _settings(500))
        asyncio.run(middleware(scope, receive, capture))
        assert capture.status == 413
        assert b"exceeds" in capture.body

    def test_ambiguous_content_length_rejected_closed(self):
        async def app(scope, receive, send):
            raise AssertionError("must not be reached")

        capture = _Capture()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/workspaces/ws/campaigns",
            "headers": [(b"content-length", b"100, 200")],
        }
        asyncio.run(CampaignBodyLimitMiddleware(app, _settings(1000))(scope, None, capture))
        assert capture.status == 413

    def test_non_campaign_path_untouched(self):
        reached: list[str] = []

        async def app(scope, receive, send):
            reached.append(scope["path"])

        capture = _Capture()
        for path in ("/api/auth/login", "/api/workspaces/ws/jobs", "/api/workspaces/campaigns"):
            scope = {"type": "http", "method": "POST", "path": path, "headers": []}
            asyncio.run(CampaignBodyLimitMiddleware(app, _settings(1))(scope, None, capture))
        assert reached == [
            "/api/auth/login",
            "/api/workspaces/ws/jobs",
            "/api/workspaces/campaigns",
        ]

    def test_max_bytes_resolves_live_from_settings(self):
        settings = _settings(100)
        middleware = CampaignBodyLimitMiddleware(lambda *a: None, settings)
        assert middleware.max_bytes == 200  # 100 × 2 headroom
        settings.executor_runtime.campaigns.manifest_max_bytes = 300
        assert middleware.max_bytes == 600  # instance-settings change applies live
