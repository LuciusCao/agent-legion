"""Workspace-scoped API intake token store (schema v83, #626).

The machine-to-machine intake channel: an external system (CMS / form /
cron / other agent) presents ``Authorization: Bearer {token_id}.{secret}``
and gets the minimal editor subset — submit runs plus run/job status reads
on the ONE workspace the token is bound to. Everything else stays refused:
``require_admin`` refuses any non-empty actor_scope (so the admin plane,
including this file's own management routes, is closed), the generic
effecting guard ``reject_studio_agent_scope`` keeps refusing every scope
type (studio-agent AND api alike — the blast radius never widens), and only
the runs router swaps in ``require_workspace_api_intake`` which admits the
api scope there while still refusing studio-agent scoped tokens.

Lifecycle mirrors the worker register tokens (EXEC-WORKERACL-001): sha256
digest only, plaintext ``{token_id}.{secret}`` returned exactly once at
issuance, plus the TTL / soft-revoke / usage-watermark columns register
tokens never needed. ``resolve`` is the hot path (one request per external
call): a single indexed SELECT validates hash, expiry and revoke state, and
``last_used_at`` is refreshed at most once a minute per token (in-memory
throttle) so a submission storm does not turn into a write storm.

This module is the auth SEMANTICS layer only (BOUNDARY-DATA-001): hashing,
hmac comparison, TTL / revoke interpretation and the refresh throttle. All
persistence goes through the JobQueries facade
(``queries.workspace_api_tokens``) — no SQL, no transaction-layer imports
here (pinned by tests/scripts/test_workspace_api_token_boundary.py).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from server.app.jobs.queries import JobQueries

# Scope marker the auth chain keys on (see dependencies.workspace_access /
# the runs router guard); deliberately distinct from STUDIO_AGENT_SCOPE so
# the generic guards keep refusing it like any other non-empty scope.
WORKSPACE_API_SCOPE = "api"

_MAX_TOKEN_LABEL_LENGTH = 128
# last_used_at refresh cadence (#626): per-token in-memory throttle. Best
# effort only — a missed write costs display freshness, never access.
_LAST_USED_THROTTLE_SECONDS = 60.0


def split_api_token(token: str) -> tuple[str, str] | None:
    """Split ``{token_id}.{secret}``; None when the shape is wrong.

    The register-token format: uuid4-hex id, urlsafe secret, one dot. A
    token without a dot can never be an API token, so get_current_user only
    attempts resolution on this shape (a session token is urlsafe and may
    contain '-', '_' — never a '.'-separated uuid hex pair).
    """
    token_id, separator, secret = token.partition(".")
    if not separator or not token_id or not secret:
        return None
    return token_id, secret


def _parse_timestamp(value: Any) -> datetime | None:
    """Row timestamp (ISO string via the row layer) back to a datetime."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


class WorkspaceApiTokenStore:
    """Issue / resolve / revoke / list workspace API intake tokens (#626).

    Auth semantics on top of the JobQueries facade: the digest computation
    and comparison, expiry interpretation, and the usage-watermark throttle.
    """

    def __init__(self, queries: JobQueries) -> None:
        self._queries = queries
        self._last_used_at_refreshed: dict[str, float] = {}
        self._throttle_lock = threading.Lock()

    def issue_api_token(
        self,
        *,
        workspace_id: str,
        label: str = "",
        expires_at: datetime | None = None,
    ) -> tuple[str, str]:
        """Issue a workspace-bound API token; returns (token_id, plaintext).

        workspace_id is required — the token IS its workspace binding, the
        whole permission model. Only the sha256 digest is stored; the
        plaintext ``{token_id}.{secret}`` is returned exactly once.
        """
        if not workspace_id:
            raise ValueError("workspace_id is required")
        if len(label) > _MAX_TOKEN_LABEL_LENGTH:
            raise ValueError(f"api token label exceeds {_MAX_TOKEN_LABEL_LENGTH} chars")
        token_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(secret.encode()).hexdigest()
        self._queries.create_workspace_api_token(
            token_id, token_hash, workspace_id, label, expires_at
        )
        return token_id, f"{token_id}.{secret}"

    def resolve_api_token(self, token: str) -> dict[str, Any] | None:
        """Resolve a presented token to its binding, or None.

        Returns ``{"token_id", "workspace_id"}`` for a live (non-revoked,
        non-expired) token whose secret matches the stored digest; None for
        unknown ids, bad secrets, revoked and expired tokens — callers treat
        all four identically (401), so the reasons are not distinguished.
        Side effect: throttled ``last_used_at`` refresh (per-token, at most
        one UPDATE per minute).
        """
        parts = split_api_token(token)
        if parts is None:
            return None
        token_id, secret = parts
        row = self._queries.get_workspace_api_token_row(token_id)
        if row is None or row["revoked_at"] is not None:
            return None
        expires_at = _parse_timestamp(row["expires_at"])
        if expires_at is not None and expires_at <= datetime.now(UTC):
            return None
        digest = hashlib.sha256(secret.encode()).hexdigest()
        if not hmac.compare_digest(digest, row["token_hash"]):
            return None
        self._refresh_last_used(token_id)
        return {"token_id": token_id, "workspace_id": str(row["workspace_id"])}

    def _refresh_last_used(self, token_id: str) -> None:
        """Stamp last_used_at at most once per throttle window per token."""
        now = time.monotonic()
        with self._throttle_lock:
            if now - self._last_used_at_refreshed.get(token_id, 0.0) < (
                _LAST_USED_THROTTLE_SECONDS
            ):
                return
            self._last_used_at_refreshed[token_id] = now
        try:
            self._queries.update_workspace_api_token_last_used(token_id)
        except Exception as exc:
            # #204 broad-except audit: the usage watermark is best-effort
            # telemetry for the admin panel — its failure modes (pool
            # exhaustion, transient connection loss) must never 500 the
            # authenticated request that already resolved. Swallow, keep the
            # throttle stamp so the broken write is not retried per request,
            # and surface the condition through the standard logger only.
            import logging

            logging.getLogger(__name__).debug(
                "workspace api token last_used_at refresh failed", exc_info=exc
            )

    def list_api_tokens(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        """List tokens (one workspace's, or all); never hash or plaintext."""
        return self._queries.list_workspace_api_tokens(workspace_id)

    def revoke_api_token(self, token_id: str, workspace_id: str | None = None) -> bool:
        """Soft-revoke a token; False when it does not exist (or belongs to
        another workspace when workspace_id is given — same False, no leak)."""
        return self._queries.revoke_workspace_api_token(token_id, workspace_id)
