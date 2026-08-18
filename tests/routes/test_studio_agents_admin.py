"""Admin registry routes for Studio chat ACP agents (phase 3 chunk 4)."""

from __future__ import annotations

CSRF = {"x-agent-legion-request": "1"}
REGISTRY_URL = "/api/admin/studio-agents"


def _member_client(client, username="registry-member", password="pw1"):
    response = client.post("/api/users", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    member = client.__class__(client.app)
    response = member.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    member.headers["x-agent-legion-request"] = "1"
    return member


def _document() -> dict:
    return {
        "api_base": "http://127.0.0.1:8000",
        "agents": [
            {
                "id": "kimi-acp",
                "label": "Kimi Code (ACP)",
                "command": "kimi",
                "args": ["acp"],
            }
        ],
    }


def test_anonymous_and_member_are_rejected(client, anon_client) -> None:
    assert anon_client.get(REGISTRY_URL).status_code == 401
    assert anon_client.put(REGISTRY_URL, json=_document()).status_code == 401
    member = _member_client(client)
    assert member.get(REGISTRY_URL).status_code == 403
    assert member.put(REGISTRY_URL, json=_document()).status_code == 403


def _strip_availability(payload: dict) -> dict[str, bool]:
    availability = payload.pop("availability")
    assert all(isinstance(value, bool) for value in availability.values())
    return availability


def test_default_document_and_roundtrip(client) -> None:
    response = client.get(REGISTRY_URL)
    assert response.status_code == 200
    assert response.json() == {
        "api_base": "http://127.0.0.1:8000",
        "agents": [],
        "availability": {},
    }

    document = _document()
    response = client.put(REGISTRY_URL, json=document)
    assert response.status_code == 200, response.text
    payload = response.json()
    availability = _strip_availability(payload)
    assert set(availability) == {"kimi-acp"}
    assert payload == document
    payload = client.get(REGISTRY_URL).json()
    availability = _strip_availability(payload)
    assert set(availability) == {"kimi-acp"}
    assert payload == document


def test_validation_rejects_bad_documents(client) -> None:
    document = _document()
    document["agents"].append(document["agents"][0].copy())
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["id"] = "BAD ID"
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["agents"][0]["command"] = ""
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["unknown"] = 1
    assert client.put(REGISTRY_URL, json=document).status_code == 422

    document = _document()
    document["api_base"] = ""
    assert client.put(REGISTRY_URL, json=document).status_code == 422
