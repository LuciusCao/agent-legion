"""Scoped token mint/authenticate/revoke semantics (STUDIO-AGENT-001)."""

from __future__ import annotations

from datetime import timedelta

from server.app.auth import scoped_tokens
from server.app.auth.sessions import hash_token
from server.app.db.transaction import read_connection
from tests.postgres_support import TEST_DATABASE_URL


def _create_user(job_db, username: str = "scoped-user") -> str:
    return str(job_db.create_user(username, password_hash=None)["id"])


def test_mint_and_authenticate_roundtrip(job_db) -> None:
    user_id = _create_user(job_db)
    token = scoped_tokens.mint_scoped_token(job_db, user_id)

    user = scoped_tokens.authenticate_scoped_token(job_db, token)

    assert user is not None
    assert user["id"] == user_id
    assert user["actor_scope"] == scoped_tokens.STUDIO_AGENT_SCOPE
    assert "password_hash" not in user


def test_only_token_hash_is_persisted(job_db) -> None:
    user_id = _create_user(job_db)
    token = scoped_tokens.mint_scoped_token(job_db, user_id)

    with read_connection(TEST_DATABASE_URL) as conn:
        row = conn.execute(
            "select token_hash, scope, revoked_at from auth_scoped_tokens"
        ).fetchone()

    assert row["token_hash"] == hash_token(token)
    assert row["token_hash"] != token
    assert row["scope"] == scoped_tokens.STUDIO_AGENT_SCOPE
    assert row["revoked_at"] is None


def test_expired_token_does_not_authenticate(job_db) -> None:
    user_id = _create_user(job_db)
    token = scoped_tokens.mint_scoped_token(job_db, user_id, ttl=timedelta(seconds=-1))

    assert scoped_tokens.authenticate_scoped_token(job_db, token) is None


def test_unknown_token_does_not_authenticate(job_db) -> None:
    assert scoped_tokens.authenticate_scoped_token(job_db, "forged-token") is None


def test_revoked_token_does_not_authenticate(job_db) -> None:
    user_id = _create_user(job_db)
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    scoped_tokens.revoke_scoped_token(job_db, token)

    assert scoped_tokens.authenticate_scoped_token(job_db, token) is None


def test_disabled_user_token_does_not_authenticate(job_db) -> None:
    user_id = _create_user(job_db)
    token = scoped_tokens.mint_scoped_token(job_db, user_id)
    job_db.update_user(user_id, disabled=True)

    assert scoped_tokens.authenticate_scoped_token(job_db, token) is None
