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
# Run duration plus grace. Deliberately fixed (no sliding renewal) so a leaked
# token dies on its own.
SCOPED_TOKEN_TTL = timedelta(hours=2)


def mint_scoped_token(
    queries: JobQueries,
    user_id: str,
    *,
    scope: str = STUDIO_AGENT_SCOPE,
    ttl: timedelta = SCOPED_TOKEN_TTL,
    origin: str = "run",
    now: datetime | None = None,
) -> str:
    """Mint a scoped bearer token for user_id; the raw token is returned once.

    origin records who minted the token: 'run' for per-run tokens (the
    default, unchanged for existing callers) and 'user' for self-service
    tokens minted via /api/studio-agent-tokens.
    """
    token = issue_token()
    expires_at = (now or datetime.now(UTC)) + ttl
    queries.create_scoped_token(hash_token(token), user_id, scope, expires_at, origin=origin)
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
