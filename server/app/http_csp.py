"""Content-Security-Policy for the HTML documents the Host serves (#752).

Every ``text/html`` response (the SPA shell, its catch-all, the
frontend-missing page) gets one policy; API JSON, SSE, raw artifact
responses and FastAPI's built-in ``/docs`` / ``/redoc`` pages are left alone (raw HTML/SVG artifacts are already forced to
download, see services/job_artifact_media). The policy is a second layer
behind DOMPurify, sized to what the shipped frontend actually loads:

- ``script-src 'self' 'unsafe-inline'``: the vite build has no inline
  script, but ``srcdoc`` iframes INHERIT the embedding document's policy —
  the workspace preview panels (``sandbox="allow-scripts"``, opaque origin)
  are single-file bundles whose scripts are inline by contract, so a
  ``'self'``-only script-src would blank every panel. Nonce-tightening the
  shell (and teaching the panel host to stamp the nonce) is the follow-up;
  until then the remaining directives still bound exfiltration and framing.
- ``style-src 'unsafe-inline'``: MUI/emotion inject ``<style>`` tags and
  KaTeX output carries inline ``style`` attributes. Google Fonts CSS and
  font files are the only third-party subresources (frontend/index.html).
- ``img-src`` keeps remote ``http(s):`` images: rendered markdown allows
  them (sanitizer hook: http(s)-only), so narrowing it would blank existing
  artifact and chat content. ``data:``/``blob:`` cover inline and object-URL images.
- ``connect-src``: same origin (``'self'`` also matches same-host ws/wss in
  CSP3; the request host is listed explicitly for engines that predate
  that) plus the object store's presign origin — material uploads PUT
  straight to the presigned URL.
- ``frame-ancestors 'self'`` closes clickjacking; ``object-src 'none'`` /
  ``base-uri 'self'`` / ``form-action 'self'`` are the usual hardening.

The preview panel iframe additionally injects its own stricter meta policy
(frontend/src/features/previewPanel/PreviewPanelHost.tsx); both apply.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from server.app.storage.s3_settings import S3Settings

CSP_HEADER = "content-security-policy"
# Presigned URLs against AWS proper (no endpoint configured) are
# virtual-hosted: https://<bucket>.s3[.<region>].amazonaws.com/... — and the
# AWS China partition (cn-* regions) signs against amazonaws.com.cn instead.
_AWS_S3_SOURCE = "https://*.amazonaws.com"
_AWS_CN_S3_SOURCE = "https://*.amazonaws.com.cn"


def object_store_connect_sources(s3: S3Settings | None) -> tuple[str, ...]:
    """The origin browsers PUT presigned material uploads to, if any."""
    if s3 is None:
        return ()
    endpoint = s3.public_endpoint_url or s3.endpoint_url
    if not endpoint:
        return (_AWS_CN_S3_SOURCE if s3.region.startswith("cn-") else _AWS_S3_SOURCE,)
    parts = urlsplit(endpoint)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ()
    # Custom endpoints presign path-style, so the endpoint origin is exact.
    return (f"{parts.scheme}://{parts.netloc}",)


def build_spa_csp(connect_sources: Sequence[str] = (), host: str = "") -> str:
    """Render the document policy; ``host`` is the request Host (may be '')."""
    socket_sources = (f"ws://{host}", f"wss://{host}") if _is_plain_host(host) else ()
    connect = " ".join(("'self'", *socket_sources, *connect_sources))
    return "; ".join(
        (
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' data: https://fonts.gstatic.com",
            "img-src 'self' data: blob: http: https:",
            "media-src 'self' data: blob:",
            f"connect-src {connect}",
            "frame-src 'self' blob: data:",
            "worker-src 'self' blob:",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'self'",
        )
    )


def _is_plain_host(host: str) -> bool:
    # A Host header is client-supplied: only echo a bare host[:port] so no
    # separator can smuggle extra directives or sources into the policy.
    return bool(host) and all(ch.isalnum() or ch in ".-:[]" for ch in host)


# FastAPI's built-in API docs pages (default docs_url / redoc_url) load their
# UI bundles from a CDN; the SPA policy is not theirs, so they are left alone.
# Exact paths only: anything else under these prefixes falls through to the
# SPA catch-all and must keep the SPA policy.
_API_DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})


def _is_api_docs_path(path: str) -> bool:
    return path in _API_DOCS_PATHS


class ContentSecurityPolicyMiddleware:
    """Attach the document CSP to ``text/html`` responses lacking one."""

    def __init__(self, app: ASGIApp, connect_sources: Sequence[str] = ()) -> None:
        self.app = app
        self.connect_sources = tuple(connect_sources)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_api_docs_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        host = Headers(scope=scope).get("host", "")

        async def send_with_csp(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                content_type = headers.get("content-type", "")
                if content_type.startswith("text/html") and CSP_HEADER not in headers:
                    headers[CSP_HEADER] = build_spa_csp(self.connect_sources, host)
            await send(message)

        await self.app(scope, receive, send_with_csp)
