"""Full-gate checks for the user auth control plane (SECURITY-AUTH-001)."""

from __future__ import annotations

import hashlib

import pytest

from tests.routes.test_auth_routes import (
    test_login_lockout_after_repeated_failures as _assert_login_lockout,
)
from tests.routes.test_auth_routes import (
    test_users_endpoints_require_admin as _assert_users_require_admin,
)


@pytest.mark.full_gate
def test_session_token_is_hashed_and_revocable(client, job_db) -> None:
    """The sessions table stores only sha256 digests; logout revokes."""
    token = client.cookies.get("agent_legion_session")
    assert token
    with job_db.connect() as conn:
        rows = conn.execute("select token_hash, revoked_at from sessions").fetchall()
    assert rows
    digest = hashlib.sha256(token.encode()).hexdigest()
    assert any(row["token_hash"] == digest for row in rows)
    assert all(token not in row["token_hash"] for row in rows)

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    with job_db.connect() as conn:
        row = conn.execute(
            "select revoked_at from sessions where token_hash=%s", (digest,)
        ).fetchone()
    assert row is not None and row["revoked_at"] is not None


@pytest.mark.full_gate
def test_management_endpoints_require_admin(anon_client) -> None:
    _assert_users_require_admin(anon_client)


@pytest.mark.full_gate
def test_login_rate_limit_lockout(anon_client) -> None:
    _assert_login_lockout(anon_client)
