from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from server.app.auth.passwords import hash_password, verify_password
from server.app.auth.rate_limit import LoginLockedError, LoginRateLimiter
from server.app.auth.sessions import hash_token, issue_token
from server.app.db.transaction import write_transaction
from tests.postgres_support import TEST_DATABASE_URL


def test_password_hash_roundtrip() -> None:
    stored = hash_password("s3cret!")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret!", stored) is True
    assert verify_password("wrong", stored) is False


def test_password_hash_uses_random_salt() -> None:
    assert hash_password("same") != hash_password("same")


def test_verify_password_rejects_missing_and_malformed_hashes() -> None:
    assert verify_password("x", None) is False
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "bcrypt$1$aa$bb") is False


def test_hash_password_requires_non_empty() -> None:
    with pytest.raises(ValueError, match="Password is required"):
        hash_password("")


def test_rate_limiter_locks_after_max_failures() -> None:
    limiter = LoginRateLimiter(max_failures=3, lock_seconds=60)
    limiter.check("alice")
    limiter.record_failure("alice")
    limiter.record_failure("alice")
    limiter.check("alice")
    limiter.record_failure("alice")
    with pytest.raises(LoginLockedError):
        limiter.check("alice")


def test_rate_limiter_success_resets_failures() -> None:
    limiter = LoginRateLimiter(max_failures=2, lock_seconds=60)
    limiter.record_failure("bob")
    limiter.record_success("bob")
    limiter.record_failure("bob")
    limiter.check("bob")


def test_rate_limiter_lock_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    # Drive the limiter with a fake monotonic clock instead of sleeping on the
    # real one: sub-second real-clock waits are flaky on loaded CI runners.
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    limiter = LoginRateLimiter(max_failures=1, lock_seconds=60)
    limiter.record_failure("carol")
    with pytest.raises(LoginLockedError):
        limiter.check("carol")
    now[0] += 61
    limiter.check("carol")


def test_session_token_digest_is_deterministic() -> None:
    token = issue_token()
    assert token
    assert hash_token(token) == hash_token(token)
    assert hash_token(token) != token


def _make_user(job_db, username="alice", role="member"):
    return job_db.create_user(
        username, display_name=username.title(), password_hash=hash_password("pw"), role=role
    )


def test_create_and_get_user(job_db) -> None:
    user = _make_user(job_db)
    assert user["role"] == "member"
    assert "password_hash" not in user
    fetched = job_db.get_user(user["id"])
    assert fetched is not None and fetched["username"] == "alice"
    creds = job_db.get_user_credentials("alice")
    assert creds is not None and verify_password("pw", creds["password_hash"])


def test_create_user_rejects_duplicate_and_bad_role(job_db) -> None:
    _make_user(job_db)
    with pytest.raises(ValueError, match="Username already exists"):
        _make_user(job_db)
    with pytest.raises(ValueError, match="Unknown user role"):
        job_db.create_user("bob", role="superuser")
    with pytest.raises(ValueError, match="Username is required"):
        job_db.create_user("  ")


def test_update_user_role_display_and_disable(job_db) -> None:
    user = _make_user(job_db)
    updated = job_db.update_user(user["id"], display_name="Alice A", role="admin")
    assert updated["display_name"] == "Alice A"
    assert updated["role"] == "admin"
    disabled = job_db.update_user(user["id"], disabled=True)
    assert disabled["disabled_at"] is not None
    enabled = job_db.update_user(user["id"], disabled=False)
    assert enabled["disabled_at"] is None
    with pytest.raises(ValueError, match="User not found"):
        job_db.update_user("missing", display_name="x")


def test_session_lifecycle(job_db) -> None:
    user = _make_user(job_db)
    token = issue_token()
    job_db.create_session(hash_token(token), user["id"])
    resolved = job_db.get_session_user(hash_token(token))
    assert resolved is not None and resolved["id"] == user["id"]
    job_db.revoke_session(hash_token(token))
    assert job_db.get_session_user(hash_token(token)) is None
    assert job_db.get_session_user(hash_token("never-issued")) is None


def test_expired_session_is_invalid(job_db) -> None:
    user = _make_user(job_db)
    token_hash = hash_token(issue_token())
    job_db.create_session(token_hash, user["id"])
    with write_transaction(TEST_DATABASE_URL) as conn:
        conn.execute(
            "update sessions set expires_at=%s where token_hash=%s",
            (datetime.now(UTC) - timedelta(seconds=1), token_hash),
        )
    assert job_db.get_session_user(token_hash) is None


def test_disabling_user_revokes_sessions(job_db) -> None:
    user = _make_user(job_db)
    token_hash = hash_token(issue_token())
    job_db.create_session(token_hash, user["id"])
    job_db.update_user(user["id"], disabled=True)
    assert job_db.get_session_user(token_hash) is None


def test_password_reset_revokes_sessions(job_db) -> None:
    user = _make_user(job_db)
    token_hash = hash_token(issue_token())
    job_db.create_session(token_hash, user["id"])
    job_db.update_user(user["id"], password_hash=hash_password("new-pw"))
    assert job_db.get_session_user(token_hash) is None


def test_workspace_membership_roundtrip(job_db) -> None:
    user = _make_user(job_db)
    workspace = job_db.create_workspace("Auth WS")
    job_db.upsert_workspace_member(workspace["id"], user["id"], "viewer")
    assert job_db.get_workspace_role(workspace["id"], user["id"]) == "viewer"
    job_db.upsert_workspace_member(workspace["id"], user["id"], "editor")
    assert job_db.get_workspace_role(workspace["id"], user["id"]) == "editor"
    members = job_db.list_workspace_members(workspace["id"])
    assert len(members) == 1
    assert members[0]["username"] == "alice"
    assert members[0]["member_role"] == "editor"
    assert job_db.list_user_workspace_ids(user["id"]) == [workspace["id"]]
    job_db.delete_workspace_member(workspace["id"], user["id"])
    assert job_db.get_workspace_role(workspace["id"], user["id"]) is None
    with pytest.raises(ValueError, match="Workspace member not found"):
        job_db.delete_workspace_member(workspace["id"], user["id"])


def test_workspace_membership_validates_refs_and_role(job_db) -> None:
    user = _make_user(job_db)
    workspace = job_db.create_workspace("Auth WS 2")
    with pytest.raises(ValueError, match="Unknown workspace member role"):
        job_db.upsert_workspace_member(workspace["id"], user["id"], "owner")
    with pytest.raises(ValueError, match="Workspace not found"):
        job_db.upsert_workspace_member("missing", user["id"], "editor")
    with pytest.raises(ValueError, match="User not found"):
        job_db.upsert_workspace_member(workspace["id"], "missing", "editor")
