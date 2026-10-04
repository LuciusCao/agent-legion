"""Unauthenticated "is this endpoint me?" probe for the Studio MCP mount (#915).

Before a chat session hands its scoped token to ``{api_base}/api/studio-agent/
mcp``, the backend checks that ``api_base`` really reaches THIS process — a
wrong port (nothing listening) and a different agent-legion instance (the
token is unknown there → 401) both made kimi-code silently drop the MCP
server, leaving the session with zero platform tools.

Challenge/response, no credential involved: the checker sends a random nonce,
the endpoint answers ``HMAC-SHA256(per-process key, nonce)``, and only the
process holding the same in-memory key can verify it. Another instance owns a
different key, so "reachable but someone else" is told apart from "me". The
probe carries no token (the token never goes to an unverified address for
probing's sake) and reveals nothing but a keyed digest of the caller's own
nonce — the key is random per process and never leaves memory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import parse_qs

from starlette.types import Receive, Scope, Send

# Served by the MCP mount's ASGI guard ahead of the token check; the path is
# relative to the mount (``/api/studio-agent``).
PROBE_SUBPATH = "/instance-probe"
_KEY = secrets.token_bytes(32)
_NONCE_RE = re.compile(r"^[0-9a-f]{16,128}$")


def instance_proof(nonce: str) -> str:
    return hmac.new(_KEY, nonce.encode("ascii"), hashlib.sha256).hexdigest()


def is_probe_request(scope: Scope, mount_path: str) -> bool:
    path = str(scope.get("path", ""))
    return scope.get("method") == "GET" and path in (
        PROBE_SUBPATH,
        f"{mount_path}{PROBE_SUBPATH}",
    )


async def serve_probe(scope: Scope, receive: Receive, send: Send) -> None:
    del receive
    query = parse_qs(bytes(scope.get("query_string", b"")).decode("latin-1"))
    nonce = (query.get("nonce") or [""])[0]
    if _NONCE_RE.fullmatch(nonce):
        status, payload = 200, {"proof": instance_proof(nonce)}
    else:
        status, payload = 400, {"detail": "nonce must be 16-128 lowercase hex chars"}
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        }
    )
    await send({"type": "http.response.body", "body": body})
