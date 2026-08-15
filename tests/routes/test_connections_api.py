"""Admin external-connection API: auth matrix, CRUD, masking, test endpoint."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

CSRF = {"x-agent-legion-request": "1"}
CONNECTIONS_URL = "/api/admin/connections"
TYPES_URL = "/api/admin/connection-types"


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


def _payload() -> dict:
    return {
        "key": "cms-internal",
        "type": "static_bearer",
        "display_name": "CMS",
        "config": {"base_url": "http://cms.example", "token": "tok-123"},
    }


def _member_client(client, username="conn_member", password="pw1"):
    response = client.post(
        "/api/users",
        json={"username": username, "password": password},
        headers=CSRF,
    )
    assert response.status_code == 201, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def test_requires_auth(anon_client) -> None:
    assert anon_client.get(CONNECTIONS_URL).status_code == 401
    assert anon_client.get(TYPES_URL).status_code == 401
    assert anon_client.post(CONNECTIONS_URL, json=_payload(), headers=CSRF).status_code == 401
    assert (
        anon_client.put(
            f"{CONNECTIONS_URL}/cms-internal", json={"enabled": False}, headers=CSRF
        ).status_code
        == 401
    )
    assert anon_client.delete(f"{CONNECTIONS_URL}/cms-internal", headers=CSRF).status_code == 401
    assert anon_client.post(f"{CONNECTIONS_URL}/cms-internal/test", headers=CSRF).status_code == 401


def test_member_forbidden(client) -> None:
    member = _member_client(client)
    assert member.get(CONNECTIONS_URL).status_code == 403
    assert member.get(TYPES_URL).status_code == 403
    assert member.post(CONNECTIONS_URL, json=_payload(), headers=CSRF).status_code == 403
    assert (
        member.put(
            f"{CONNECTIONS_URL}/cms-internal", json={"enabled": False}, headers=CSRF
        ).status_code
        == 403
    )
    assert member.delete(f"{CONNECTIONS_URL}/cms-internal", headers=CSRF).status_code == 403
    assert member.post(f"{CONNECTIONS_URL}/cms-internal/test", headers=CSRF).status_code == 403


def test_types_listed(client) -> None:
    response = client.get(TYPES_URL)
    assert response.status_code == 200
    types = {t["type"]: t for t in response.json()["types"]}
    assert types["static_bearer"]["secret_keys"] == ["token"]


def test_create_get_mask_round_trip(client) -> None:
    response = client.post(CONNECTIONS_URL, json=_payload(), headers=CSRF)
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["config"]["token"] == {"secret_set": True}
    assert view["config"]["base_url"] == "http://cms.example"

    listing = client.get(CONNECTIONS_URL).json()["connections"]
    assert [c["key"] for c in listing] == ["cms-internal"]
    assert listing[0]["token"] is None  # never acquired yet


def test_create_validation_errors(client) -> None:
    bad_key = {**_payload(), "key": "Bad Key"}
    assert client.post(CONNECTIONS_URL, json=bad_key, headers=CSRF).status_code == 400
    bad_type = {**_payload(), "type": "nope"}
    assert client.post(CONNECTIONS_URL, json=bad_type, headers=CSRF).status_code == 400
    response = client.post(CONNECTIONS_URL, json=_payload(), headers=CSRF)
    assert response.status_code == 200
    assert client.post(CONNECTIONS_URL, json=_payload(), headers=CSRF).status_code == 409


def test_update_and_delete(client) -> None:
    assert client.post(CONNECTIONS_URL, json=_payload(), headers=CSRF).status_code == 200

    response = client.put(
        f"{CONNECTIONS_URL}/cms-internal",
        json={"config": {"base_url": "http://cms2.example", "token": {"secret_set": True}}},
        headers=CSRF,
    )
    assert response.status_code == 200, response.text
    assert response.json()["config"]["base_url"] == "http://cms2.example"

    response = client.delete(f"{CONNECTIONS_URL}/cms-internal", headers=CSRF)
    assert response.status_code == 200
    assert client.get(CONNECTIONS_URL).json()["connections"] == []
    assert client.delete(f"{CONNECTIONS_URL}/cms-internal", headers=CSRF).status_code == 404


def test_probe_without_probe_url(client) -> None:
    assert client.post(CONNECTIONS_URL, json=_payload(), headers=CSRF).status_code == 200
    response = client.post(f"{CONNECTIONS_URL}/cms-internal/test", headers=CSRF)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True


def test_probe_unknown_connection_404(client) -> None:
    assert client.post(f"{CONNECTIONS_URL}/nope/test", headers=CSRF).status_code == 404
