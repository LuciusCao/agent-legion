"""HTTP middleware wiring for the FastAPI app."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.datastructures import Headers
from starlette.middleware.gzip import IdentityResponder
from starlette.types import ASGIApp, Receive, Scope, Send

from server.app.http_gzip import SelectiveGZipResponder
from server.app.http_request_id import RequestIdMiddleware
from server.app.settings import Settings


class SelectiveGZipMiddleware(GZipMiddleware):
    """GZip responses, except Range requests, .zip downloads, and payloads
    that are already compressed (application/gzip bundles and archives).

    Range responses (e.g. video seeking) must stay byte-exact; recompressing
    archives only burns CPU and strips Content-Length.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if "range" in Headers(scope=scope) or scope["path"].endswith(".zip"):
            await self.app(scope, receive, send)
            return
        responder: ASGIApp
        if "gzip" in Headers(scope=scope).get("Accept-Encoding", ""):
            responder = SelectiveGZipResponder(
                self.app, self.minimum_size, compresslevel=self.compresslevel
            )
        else:
            responder = IdentityResponder(self.app, self.minimum_size)
        await responder(scope, receive, send)


def add_http_middleware(app: FastAPI, settings: Settings) -> None:
    """Register CORS and gzip middleware (last added runs outermost)."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors.allow_origins),
        allow_credentials=settings.cors.allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Level 6 is the zlib sweet spot: ~3-4x faster than the starlette default
    # 9 for a few percent worse ratio. Compression runs synchronously on the
    # event loop, so the CPU saved here is loop latency for every SSE/WS peer.
    app.add_middleware(SelectiveGZipMiddleware, compresslevel=6)
    # Request-id correlation + slow-request logging (#273). Added last, so it
    # runs outermost: every response (CORS preflight included) carries the id,
    # and the slow-request timing covers the full app stack, not just the
    # router. See http_request_id.py for the pass-through/template tradeoffs.
    app.add_middleware(RequestIdMiddleware)
