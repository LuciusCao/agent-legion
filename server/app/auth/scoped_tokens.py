"""Short-lived scope-bound bearer tokens for server-side built-in agents.

A studio chat run mints one scoped token per run — bound to the initiating
user and ``actor_scope='studio_agent'`` with a TTL of roughly run duration
plus grace — instead of handing the agent a full user session
(STUDIO-AGENT-001). Scoped tokens authenticate only through the Bearer
channel; effecting endpoints (publish/rollback/archive, job lifecycle and
execution triggers, workspace/secret/package/member/settings writes, worker
pause/resume) mount ``reject_studio_agent_scope`` to refuse them explicitly,
``require_admin`` refuses scoped identities outright, and draft/validate
endpoints stay reachable. Like sessions, only the sha256 digest is persisted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.auth.sessions import hash_token, issue_token
from server.app.jobs.queries import JobQueries

STUDIO_AGENT_SCOPE = "studio_agent"
# Run duration plus grace. The TTL is fixed at mint time, but a live studio
# chat session slides it forward at each turn start (renew_scoped_token below,
# #158): the token the agent holds in its MCP headers cannot be swapped
# mid-session, so the same token's expiry is extended while a human keeps the
# session active. A leaked token still dies on its own once the session goes
# idle or closes (close revokes it outright).
SCOPED_TOKEN_TTL = timedelta(hours=2)
# Renew at turn start only when less than this much life remains, so active
# sessions do not pay an UPDATE per turn.
SCOPED_TOKEN_RENEW_THRESHOLD = timedelta(minutes=30)


def mint_scoped_token(
    queries: JobQueries,
    user_id: str,
    *,
    scope: str = STUDIO_AGENT_SCOPE,
    ttl: timedelta = SCOPED_TOKEN_TTL,
    origin: str = "run",
    workspace_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """Mint a scoped bearer token for user_id; the raw token is returned once.

    origin: 'run' (per-run, the default) or 'user' (self-service via
    /api/studio-agent-tokens). workspace_id binds a run token to the chat
    session's workspace (schema v45), written atomically in the same INSERT.
    """
    token = issue_token()
    expires_at = (now or datetime.now(UTC)) + ttl
    queries.create_scoped_token(
        hash_token(token), user_id, scope, expires_at, origin=origin, workspace_id=workspace_id
    )
    return token


def authenticate_scoped_token(queries: JobQueries, token: str) -> dict[str, Any] | None:
    """Resolve a raw scoped token to its user plus actor_scope, or None."""
    record = queries.get_scoped_token_user(hash_token(token))
    if record is None:
        return None
    scope = str(record.pop("scope"))
    return {**record, "actor_scope": scope}


def revoke_scoped_token(queries: JobQueries, token: str) -> None:
    """Revoke a scoped token (run finished or cancelled)."""
    queries.revoke_scoped_token(hash_token(token))


def renew_scoped_token(
    queries: JobQueries,
    token: str,
    *,
    ttl: timedelta = SCOPED_TOKEN_TTL,
    threshold: timedelta = SCOPED_TOKEN_RENEW_THRESHOLD,
    now: datetime | None = None,
) -> None:
    """Slide a live token's expiry a full TTL forward when close to expiry.

    Called at studio chat turn start (#158): chat sessions outlive the fixed
    TTL, and the agent's MCP headers cannot be re-pointed mid-session, so the
    same token is kept alive while the human keeps prompting. No-op for
    revoked tokens, tokens with more than ``threshold`` life left, and —
    deliberately — already-expired tokens: an idle session's leaked token
    must not spring back to life on the next prompt.
    """
    current = now or datetime.now(UTC)
    queries.extend_scoped_token_expiry(hash_token(token), current + ttl, current + threshold)
