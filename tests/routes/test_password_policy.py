"""New-password baseline (#970): length floor + common-password refusal on
every set-password entry point, never on login.

The suite-wide harness relaxes the length floor for fixture accounts
(tests/conftest.py ``_fast_password_hashing``); the ``floor`` fixture here
restores the production value per test.
"""

from __future__ import annotations

import pytest

from server.app.auth import password_policy
from server.app.auth.password_policy import WeakPasswordError, validate_new_password
from server.app.auth.passwords import hash_password

CSRF = {"x-agent-legion-request": "1"}
# Captured at import, before the autouse harness relaxation patches it.
_PRODUCTION_FLOOR = password_policy.MIN_PASSWORD_LENGTH
STRONG = "orbit-lantern-quiet-47"


@pytest.fixture
def floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request AFTER ``client`` so the fixture admin bootstraps first."""
    monkeypatch.setattr(password_policy, "MIN_PASSWORD_LENGTH", _PRODUCTION_FLOOR)


@pytest.mark.no_db
@pytest.mark.parametrize(
    "password",
    ["short", "elevenchars", "Password1234", " qwerty123456 ", "111111111111", "AdminAdmin123"],
)
def test_weak_passwords_are_refused(floor, password: str) -> None:
    with pytest.raises(WeakPasswordError):
        validate_new_password(password)


@pytest.mark.no_db
@pytest.mark.parametrize("password", [STRONG, "twelve-chars", "correct horse battery"])
def test_reasonable_passwords_pass(floor, password: str) -> None:
    validate_new_password(password)


def test_bootstrap_refuses_a_weak_password_and_stays_available(anon_client, floor) -> None:
    response = anon_client.post(
        "/api/auth/bootstrap", json={"username": "admin", "password": "admin-pw"}
    )
    assert response.status_code == 400
    assert f"at least {_PRODUCTION_FLOOR} characters" in response.json()["detail"]
    assert anon_client.get("/api/auth/bootstrap").json() == {"available": True}
    ok = anon_client.post("/api/auth/bootstrap", json={"username": "admin", "password": STRONG})
    assert ok.status_code == 200, ok.text


def test_admin_create_and_reset_apply_the_policy(client, floor) -> None:
    weak = client.post(
        "/api/users", json={"username": "m1", "password": "password1234"}, headers=CSRF
    )
    assert weak.status_code == 400
    assert "too common" in weak.json()["detail"]
    created = client.post("/api/users", json={"username": "m1", "password": STRONG}, headers=CSRF)
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    reset = client.patch(f"/api/users/{user_id}", json={"password": "short-pw"}, headers=CSRF)
    assert reset.status_code == 400
    # A refused reset leaves the old password in force.
    member = client.__class__(client.app)
    login = member.post("/api/auth/login", json={"username": "m1", "password": STRONG})
    assert login.status_code == 200


def test_existing_weak_password_accounts_still_log_in(client, floor) -> None:
    """The floor gates only NEW passwords: an account whose stored hash
    predates the policy keeps logging in with it."""
    job_db = client.app.state.job_db
    job_db.create_user("legacy", password_hash=hash_password("pw1"), role="member")
    legacy = client.__class__(client.app)
    login = legacy.post("/api/auth/login", json={"username": "legacy", "password": "pw1"})
    assert login.status_code == 200, login.text


def test_weak_env_seed_password_fails_startup(client_factory, floor, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD", "seeded-pw")
    with (
        pytest.raises(ValueError, match="BOOTSTRAP_ADMIN_PASSWORD rejected"),
        client_factory(authenticated=False, fresh=True),
    ):
        pass
