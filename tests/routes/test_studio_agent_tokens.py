"""Contract tests for /api/studio-agent-tokens (self-service scoped tokens).

Users mint, list, and revoke their own origin='user' studio-agent tokens for
external agents (e.g. the MCP server). The raw token is returned exactly once
at mint; the management views never expose digest or plaintext. Run-scoped
tokens (origin='run') are refused management access — a 2h run token must not
mint 720h credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime

from server.app.auth import scoped_tokens

CSRF = {"x-agent-legion-request": "1"}


def _bearer_client(client, token: str):
    bearer = client.__class__(client.app)
    bearer.headers["authorization"] = f"Bearer {token}"
    return bearer


def _mint(client, ttl_hours: int | None = None) -> dict:
    body = {} if ttl_hours is None else {"ttl_hours": ttl_hours}
    response = client.post("/api/studio-agent-tokens", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_anonymous_callers_get_401(anon_client) -> None:
    assert anon_client.post("/api/studio-agent-tokens", json={}).status_code == 401
    assert anon_client.get("/api/studio-agent-tokens").status_code == 401
    assert anon_client.delete("/api/studio-agent-tokens/some-id").status_code == 401


def test_mint_list_revoke_flow(client) -> None:
    minted = _mint(client)
    assert set(minted) == {"id", "token", "expires_at"}
    assert minted["token"]
    expires_at = datetime.fromisoformat(minted["expires_at"])
    age_seconds = (expires_at - datetime.now(UTC)).total_seconds()
    assert 160 * 3600 < age_seconds <= 168 * 3600  # default ttl_hours=168

    listed = client.get("/api/studio-agent-tokens")
    assert listed.status_code == 200, listed.text
    entries = listed.json()["tokens"]
    assert [entry["id"] for entry in entries] == [minted["id"]]
    entry = entries[0]
    # Management view: public id + timestamps only, never token material.
    assert set(entry) == {"id", "created_at", "expires_at", "revoked_at"}
    assert entry["revoked_at"] is None

    revoked = client.delete(f"/api/studio-agent-tokens/{minted['id']}")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json() == {"id": minted["id"], "revoked": True}

    after = client.get("/api/studio-agent-tokens").json()["tokens"]
    assert after[0]["revoked_at"] is not None
    # Revoking an already-revoked token reports not found.
    assert client.delete(f"/api/studio-agent-tokens/{minted['id']}").status_code == 404


def test_ttl_boundaries(client) -> None:
    assert client.post("/api/studio-agent-tokens", json={"ttl_hours": 0}).status_code == 422
    assert client.post("/api/studio-agent-tokens", json={"ttl_hours": 721}).status_code == 422
    for ttl_hours in (1, 720):
        minted = _mint(client, ttl_hours)
        expires_at = datetime.fromisoformat(minted["expires_at"])
        age_seconds = (expires_at - datetime.now(UTC)).total_seconds()
        assert (ttl_hours - 1) * 3600 < age_seconds <= ttl_hours * 3600


def test_unknown_token_id_gets_404(client) -> None:
    assert client.delete("/api/studio-agent-tokens/no-such-id").status_code == 404


def test_cross_user_revoke_returns_404(client) -> None:
    minted = _mint(client)
    created = client.post(
        "/api/users", json={"username": "member1", "password": "pw1"}, headers=CSRF
    )
    assert created.status_code == 201, created.text
    member = client.__class__(client.app)
    login = member.post("/api/auth/login", json={"username": "member1", "password": "pw1"})
    assert login.status_code == 200, login.text
    member.headers["x-agent-legion-request"] = "1"

    # Another user's token is indistinguishable from an unknown id.
    assert member.delete(f"/api/studio-agent-tokens/{minted['id']}").status_code == 404
    # And the other user's list stays empty.
    assert member.get("/api/studio-agent-tokens").json()["tokens"] == []


def test_minted_token_calls_tool_surface(client) -> None:
    minted = _mint(client)
    bearer = _bearer_client(client, minted["token"])

    tools = bearer.get("/api/studio-agent/tools/workflows")
    assert tools.status_code == 200, tools.text
    # The scoped token stays refused on effecting (publish-side) endpoints.
    assert bearer.post("/api/agent-definitions/some-agent/publish").status_code == 403

    client.delete(f"/api/studio-agent-tokens/{minted['id']}")
    assert bearer.get("/api/studio-agent/tools/workflows").status_code == 401


def test_run_scoped_token_cannot_manage_user_tokens(client, job_db) -> None:
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    run_token = scoped_tokens.mint_scoped_token(job_db, admin_id)
    bearer = _bearer_client(client, run_token)

    assert bearer.post("/api/studio-agent-tokens", json={}).status_code == 403
    assert bearer.get("/api/studio-agent-tokens").status_code == 403
    assert bearer.delete("/api/studio-agent-tokens/some-id").status_code == 403


def test_run_origin_tokens_are_not_listed(client, job_db) -> None:
    admin_id = str(job_db.get_user_credentials("admin")["id"])
    scoped_tokens.mint_scoped_token(job_db, admin_id)  # origin='run' default

    minted = _mint(client)
    entries = client.get("/api/studio-agent-tokens").json()["tokens"]
    assert [entry["id"] for entry in entries] == [minted["id"]]
