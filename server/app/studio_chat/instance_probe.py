"""Credential-free "is this endpoint me?" proof for the api_base self-check (#915).

Before a chat session hands its scoped token to ``{api_base}/api/studio-agent/
mcp``, the backend checks that ``api_base`` really reaches THIS process — a
wrong port (nothing listening) and a different agent-legion instance (the
token is unknown there → 401) both made kimi-code silently drop the MCP
server, leaving the session with zero platform tools.

Challenge/response rides the existing public ``GET /api/health`` (no new
anonymous endpoint, AGENTS.md §6): ``?instance_probe=<nonce>`` adds
``instance_proof = HMAC-SHA256(per-process key, nonce)`` to the unchanged
health body; without the parameter, or with an invalid nonce, the response
is byte-for-byte the old one. Only the process holding the same in-memory key
can verify the proof, so "reachable but another instance" is told apart from
"me". The probe carries no credential and reveals nothing but a keyed digest
of the caller's own nonce — the key is random per process and never leaves
memory.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

HEALTH_PATH = "/api/health"
PROBE_PARAM = "instance_probe"
_KEY = secrets.token_bytes(32)
_NONCE_RE = re.compile(r"^[0-9a-f]{16,128}$")


def valid_nonce(nonce: str | None) -> bool:
    return nonce is not None and _NONCE_RE.fullmatch(nonce) is not None


def instance_proof(nonce: str) -> str:
    return hmac.new(_KEY, nonce.encode("ascii"), hashlib.sha256).hexdigest()
