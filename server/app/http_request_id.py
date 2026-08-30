"""X-Request-Id correlation + slow-request logging middleware (#273).

Design notes (issue #273):

- Pass-through semantics: an upstream ``X-Request-Id`` consisting solely of
  ``[A-Za-z0-9-_]`` (capped at 128 chars) is echoed back verbatim so a load
  balancer's trace id survives into this app; anything else — foreign
  charset, newlines/ANSI escapes, oversized values — is *replaced* with a
  fresh hex token, not rejected (the response must still carry a usable id).
  The charset guard exists because request ids end up verbatim in log
  lines, and an unvalidated header is a log-injection vector.
- The id additionally lands in a contextvar so handlers and the layers
  below can attach it to their own log records via
  :func:`current_request_id` (None outside a request: startup code, worker
  threads). Wiring it into every module's logger format is left for later;
  the slow-request warning below is the first consumer.
- Slow-request logging uses the route template (``scope["route"]``, set by
  the router before the handler runs and visible to this outer middleware
  on the way out), e.g. ``/workspaces/{id}/jobs`` — not the raw path.
  Per-resource paths are unbounded cardinality: worthless to aggregate and
  a log-store filler. Duration is measured to ``http.response.start`` on
  purpose: SSE streams stay open for minutes *by design*, and flagging
  every open stream as a slow request would drown the signal.

Fast-path discipline (see http_middleware.py's gzip note): this runs for
every request and does only in-memory work — one header lookup, one
frozenset check, one perf_counter call, one token_hex on a miss, one list
append on the response start message. No dict copies, no regex, no
blocking calls, so the event loop is never yielded to the OS.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from contextvars import ContextVar

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID_BYTES = b"x-request-id"
# Charset allowlist for passed-through ids (log-injection guard, #273).
_REQUEST_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_MAX_REQUEST_ID_LENGTH = 128

_DEFAULT_SLOW_REQUEST_MS = 1000.0
_SLOW_REQUEST_MS_ENV = "AGENT_LEGION_SLOW_REQUEST_MS"

_request_id_var: ContextVar[str | None] = ContextVar("agent_legion_request_id", default=None)


def current_request_id() -> str | None:
    """The request id for the current task, or None outside a request."""
    return _request_id_var.get()


def slow_request_threshold_ms() -> float:
    """Slow-request threshold in ms; malformed env overrides are ignored."""
    raw = os.environ.get(_SLOW_REQUEST_MS_ENV, "")
    try:
        return float(raw) if raw else _DEFAULT_SLOW_REQUEST_MS
    except ValueError:
        return _DEFAULT_SLOW_REQUEST_MS


def _sanitize_request_id(value: str) -> str | None:
    """A pass-through-safe request id, or None when it must be regenerated."""
    if not value or len(value) > _MAX_REQUEST_ID_LENGTH:
        return None
    if _REQUEST_ID_CHARS.issuperset(value):
        return value
    return None


class RequestIdMiddleware:
    """Assign/corroborate ``X-Request-Id`` and warn on slow requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Env read once at app construction (the pools.py precedent), not
        # per request: os.environ lookups on the hot path add up.
        self.slow_request_ms = slow_request_threshold_ms()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header_value = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        request_id = _sanitize_request_id(header_value) or secrets.token_hex(8)
        start = time.perf_counter()
        slow_ms = self.slow_request_ms
        token = _request_id_var.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                # In-place append on the existing header list: no copy, and
                # no app response sets this header today (a collision would
                # surface as a duplicated header, visible in tests).
                message.setdefault("headers", []).append(
                    (_REQUEST_ID_BYTES, request_id.encode("ascii"))
                )
                duration_ms = (time.perf_counter() - start) * 1000.0
                if duration_ms > slow_ms:
                    route = scope.get("route")
                    path = getattr(route, "path", None) or scope.get("path", "")
                    logger.warning(
                        "slow request: method=%s path=%s status=%s "
                        "duration_ms=%.0f threshold_ms=%.0f request_id=%s",
                        scope.get("method", ""),
                        path,
                        message["status"],
                        duration_ms,
                        slow_ms,
                        request_id,
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            _request_id_var.reset(token)
