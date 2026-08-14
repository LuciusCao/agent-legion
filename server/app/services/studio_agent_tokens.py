"""Self-service studio-agent scoped tokens (STUDIO-AGENT-001, schema v42).

Run-scoped tokens (origin='run') are minted by the backend per studio chat
run with a fixed short TTL. External agents (e.g. the MCP server in
``server.app.mcp_server``) need longer-lived credentials the user can mint,
inspect, and revoke themselves — that is the origin='user' family managed
here. Like all scoped tokens only the sha256 digest is persisted; the raw
token is returned exactly once at mint time and the management views never
expose digest or plaintext.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.auth.scoped_tokens import STUDIO_AGENT_SCOPE
from server.app.auth.sessions import hash_token, issue_token
from server.app.jobs import JobQueries

USER_TOKEN_ORIGIN = "user"
DEFAULT_TTL_HOURS = 168
MAX_TTL_HOURS = 720


class StudioAgentTokensService:
    """Mint/list/revoke a user's own studio-agent scoped tokens."""

    def __init__(self, queries: JobQueries):
        self._queries = queries

    def mint(self, user_id: str, *, ttl_hours: int = DEFAULT_TTL_HOURS) -> dict[str, Any]:
        """Mint a user-origin token; the raw token appears only in this return."""
        if not 1 <= ttl_hours <= MAX_TTL_HOURS:
            raise ValueError(f"ttl_hours must be between 1 and {MAX_TTL_HOURS}")
        token = issue_token()
        expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours)
        token_id = self._queries.create_scoped_token(
            hash_token(token),
            user_id,
            STUDIO_AGENT_SCOPE,
            expires_at,
            origin=USER_TOKEN_ORIGIN,
        )
        return {"id": token_id, "token": token, "expires_at": expires_at.isoformat()}

    def list(self, user_id: str) -> list[dict[str, Any]]:
        """Management view of the user's tokens (id/timestamps only)."""
        return self._queries.list_scoped_tokens(user_id, origin=USER_TOKEN_ORIGIN)

    def revoke(self, user_id: str, token_id: str) -> bool:
        """Revoke one of the user's own tokens; False hides foreign/unknown ids."""
        return self._queries.revoke_scoped_token_by_id(user_id, token_id, origin=USER_TOKEN_ORIGIN)
