from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

SESSION_TTL = timedelta(days=7)
_TOKEN_BYTES = 32


def issue_token() -> str:
    """Generate a new bearer session token (returned to the client once)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Digest persisted in the sessions table; the raw token never is."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + SESSION_TTL
