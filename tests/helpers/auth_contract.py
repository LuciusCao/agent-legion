"""Shared auth contract assertions.

The unit tests in tests/routes/test_auth_routes.py and the full-gate evidence
in tests/full/test_auth_control_plane.py assert the same contracts; the
assertion bodies live here so the full-gate file does not import a unit test
module.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

CSRF = {"x-agent-legion-request": "1"}


def bootstrap_admin(client: TestClient, username: str = "admin", password: str = "admin-pw"):
    return client.post(
        "/api/auth/bootstrap",
        json={"username": username, "password": password, "display_name": "Admin"},
    )


def login(client: TestClient, username: str = "admin", password: str = "admin-pw"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def assert_login_lockout_after_repeated_failures(client: TestClient) -> None:
    bootstrap_admin(client)
    client.cookies.clear()
    for _ in range(5):
        assert login(client, password="wrong").status_code == 401
    locked = login(client)
    assert locked.status_code == 429


def assert_users_endpoints_require_admin(client: TestClient) -> None:
    bootstrap_admin(client)
    client.post(
        "/api/users",
        json={"username": "member1", "password": "pw1"},
        headers=CSRF,
    )
    member_client = client.__class__(client.app)
    member_client.post("/api/auth/login", json={"username": "member1", "password": "pw1"})
    assert member_client.get("/api/users").status_code == 403
    assert (
        member_client.post(
            "/api/users", json={"username": "x", "password": "y"}, headers=CSRF
        ).status_code
        == 403
    )
