"""Process-observed serving address → default Studio MCP callback base (#915).

The Studio agent registry's ``api_base`` tells each chat session's agent where
the agent-legion MCP endpoint lives. When an admin never configured it, the
registry used to fall back to a hard-coded ``http://127.0.0.1:8000`` — the
prod port, which every dev/worktree backend deliberately avoids. kimi-code
then silently drops the unreachable MCP server and the session runs with zero
platform tools (#915).

The fallback is now derived from the address this process actually serves
on: a pure ASGI middleware records ``scope["server"]`` — the LOCAL socket
address of the accepted connection (uvicorn fills it from ``getsockname()``).
It is never the client-controlled ``Host`` header, so it cannot steer the
scoped-token egress target (#158 boundary). Only IP literals are accepted
(test clients report ``testserver``), wildcard addresses map to loopback, and
a loopback observation wins over a non-loopback one (the agent subprocess is
always spawned on this host). An explicitly configured ``api_base`` keeps
priority (registry.get); before the first request nothing is observed and the
legacy constant remains the last resort.
"""

from __future__ import annotations

import ipaddress
import threading

from starlette.types import ASGIApp, Receive, Scope, Send

_lock = threading.Lock()
_observed: tuple[str, int] | None = None


def _normalize_host(host: str) -> str | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if address.is_unspecified:
        return "127.0.0.1"
    return str(address)


def _is_loopback(host: str) -> bool:
    return ipaddress.ip_address(host).is_loopback


def observe_server_address(server: object) -> None:
    """Record one connection's local (host, port); cheap after loopback is seen."""
    global _observed
    current = _observed
    if current is not None and _is_loopback(current[0]):
        return
    if not isinstance(server, (tuple, list)) or len(server) != 2:
        return
    raw_host, port = server
    if not isinstance(raw_host, str) or not isinstance(port, int) or not 0 < port < 65536:
        return
    host = _normalize_host(raw_host)
    if host is None:
        return
    with _lock:
        if _observed is None or (_is_loopback(host) and not _is_loopback(_observed[0])):
            _observed = (host, port)


def derived_api_base() -> str | None:
    """``http://<observed host>:<port>``, or None before any request was served."""
    observed = _observed
    if observed is None:
        return None
    host, port = observed
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}"


def reset_serving_address_for_tests() -> None:
    global _observed
    with _lock:
        _observed = None


class ServingAddressMiddleware:
    """Pure ASGI pass-through recording the serving socket address."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            observe_server_address(scope.get("server"))
        await self.app(scope, receive, send)
