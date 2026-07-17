"""HTTP middleware wiring for the FastAPI app."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from server.app.settings import Settings


class SelectiveGZipMiddleware:
    """GZip responses, except Range requests and .zip downloads.

    Range responses (e.g. video seeking) must stay byte-exact, and zip
    archives are already compressed — gzipping them only burns CPU and
    strips Content-Length.
    """

    def __init__(self, app: ASGIApp, minimum_size: int = 500) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if "range" in Headers(scope=scope) or scope["path"].endswith(".zip"):
            await self.app(scope, receive, send)
            return
        await self.gzip(scope, receive, send)


def add_http_middleware(app: FastAPI, settings: Settings) -> None:
    """Register CORS and gzip middleware (last added runs outermost)."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors.allow_origins),
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SelectiveGZipMiddleware)
