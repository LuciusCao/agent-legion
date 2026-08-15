from __future__ import annotations

CSRF = {"x-agent-legion-request": "1"}


def _bootstrap_admin(client, username="admin", password="admin-pw"):
    return client.post(
        "/api/auth/bootstrap",
        json={"username": username, "password": password, "display_name": "Admin"},
    )


def _login(client, username="admin", password="admin-pw"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_bootstrap_only_available_for_first_user(anon_client) -> None:
    status = anon_client.get("/api/auth/bootstrap")
    assert status.status_code == 200
    assert status.json() == {"available": True}

    created = _bootstrap_admin(anon_client)
    assert created.status_code == 200
    body = created.json()["user"]
    assert body["username"] == "admin"
    assert body["role"] == "admin"
    assert "password_hash" not in body

    assert anon_client.get("/api/auth/bootstrap").json() == {"available": False}
    again = _bootstrap_admin(anon_client, username="second")
    assert again.status_code == 409


def test_login_me_logout_roundtrip(anon_client) -> None:
    _bootstrap_admin(anon_client)
    anon_client.cookies.clear()

    bad = _login(anon_client, password="wrong")
    assert bad.status_code == 401

    ok = _login(anon_client)
    assert ok.status_code == 200
    assert ok.json()["user"]["username"] == "admin"

    me = anon_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "admin"

    out = anon_client.post("/api/auth/logout", headers=CSRF)
    assert out.status_code == 200
    assert anon_client.get("/api/auth/me").status_code == 401


def test_me_requires_authentication(anon_client) -> None:
    assert anon_client.get("/api/auth/me").status_code == 401


def test_cookie_mutation_requires_csrf_header(anon_client) -> None:
    _bootstrap_admin(anon_client)
    forbidden = anon_client.post("/api/auth/logout")
    assert forbidden.status_code == 403
    # Bearer channel is not ambient and stays exempt from the CSRF header.
    token = anon_client.cookies.get("agent_legion_session")
    assert token
    anon_client.cookies.clear()
    bearer = anon_client.post("/api/auth/logout", headers={"authorization": f"Bearer {token}"})
    assert bearer.status_code == 200


def test_login_lockout_after_repeated_failures(anon_client) -> None:
    _bootstrap_admin(anon_client)
    anon_client.cookies.clear()
    for _ in range(5):
        assert _login(anon_client, password="wrong").status_code == 401
    locked = _login(anon_client)
    assert locked.status_code == 429


def test_admin_user_management(anon_client) -> None:
    _bootstrap_admin(anon_client)

    created = anon_client.post(
        "/api/users",
        json={"username": "member1", "password": "pw1", "role": "member"},
        headers=CSRF,
    )
    assert created.status_code == 201
    member = created.json()
    assert member["role"] == "member"

    users = anon_client.get("/api/users")
    assert users.status_code == 200
    assert {u["username"] for u in users.json()["users"]} == {"admin", "member1"}

    patched = anon_client.patch(
        f"/api/users/{member['id']}",
        json={"display_name": "Member One", "disabled": True},
        headers=CSRF,
    )
    assert patched.status_code == 200
    assert patched.json()["disabled_at"] is not None

    # Disabled users can no longer log in.
    member_client = anon_client.__class__(anon_client.app)
    assert (
        member_client.post(
            "/api/auth/login", json={"username": "member1", "password": "pw1"}
        ).status_code
        == 401
    )

    duplicate = anon_client.post(
        "/api/users",
        json={"username": "member1", "password": "pw2"},
        headers=CSRF,
    )
    assert duplicate.status_code == 400


def test_users_endpoints_require_admin(anon_client) -> None:
    _bootstrap_admin(anon_client)
    anon_client.post(
        "/api/users",
        json={"username": "member1", "password": "pw1"},
        headers=CSRF,
    )
    member_client = anon_client.__class__(anon_client.app)
    member_client.post("/api/auth/login", json={"username": "member1", "password": "pw1"})
    assert member_client.get("/api/users").status_code == 403
    assert (
        member_client.post(
            "/api/users", json={"username": "x", "password": "y"}, headers=CSRF
        ).status_code
        == 403
    )


def test_workspace_member_management(anon_client, job_db) -> None:
    _bootstrap_admin(anon_client)
    member_resp = anon_client.post(
        "/api/users",
        json={"username": "member1", "password": "pw1"},
        headers=CSRF,
    )
    member_id = member_resp.json()["id"]
    workspace = job_db.create_workspace(
        default_workflow_key="question_comprehension_info", name="Auth Route WS"
    )

    put = anon_client.put(
        f"/api/workspaces/{workspace['id']}/members",
        json={"user_id": member_id, "role": "viewer"},
        headers=CSRF,
    )
    assert put.status_code == 200
    members = put.json()["members"]
    assert len(members) == 1
    assert members[0]["member_role"] == "viewer"
    assert members[0]["username"] == "member1"

    listed = anon_client.get(f"/api/workspaces/{workspace['id']}/members")
    assert listed.status_code == 200
    assert len(listed.json()["members"]) == 1

    missing_workspace = anon_client.put(
        "/api/workspaces/nope/members",
        json={"user_id": member_id, "role": "editor"},
        headers=CSRF,
    )
    assert missing_workspace.status_code == 404

    deleted = anon_client.delete(
        f"/api/workspaces/{workspace['id']}/members/{member_id}", headers=CSRF
    )
    assert deleted.status_code == 200
    assert deleted.json()["members"] == []


def test_bootstrap_admin_env_seed(client_factory, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_BOOTSTRAP_ADMIN_PASSWORD", "seeded-pw")
    # fresh=True: the env seed runs in build_auth_service at app creation, so
    # this test needs an app built after the monkeypatch, not the shared one.
    with client_factory(authenticated=False, fresh=True) as seeded_client:
        assert seeded_client.get("/api/auth/bootstrap").json() == {"available": False}
        assert _login(seeded_client, password="seeded-pw").status_code == 200


def test_login_unknown_user_does_not_leak(anon_client) -> None:
    _bootstrap_admin(anon_client)
    anon_client.cookies.clear()
    unknown = _login(anon_client, username="ghost")
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == "Invalid username or password"
