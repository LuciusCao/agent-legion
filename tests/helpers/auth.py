from __future__ import annotations

from fastapi.testclient import TestClient


def authenticate_client(client: TestClient) -> TestClient:
    """Log the client in as an admin and attach the CSRF header.

    Bootstraps the first admin when the database is still empty, otherwise
    falls back to login — safe for tests that build several apps/clients
    against the same schema. For tests that build their own app/TestClient
    instead of using the conftest ``client`` fixture.
    """
    credentials = {"username": "admin", "password": "admin-pw"}
    response = client.post("/api/auth/bootstrap", json=credentials)
    if response.status_code == 409:
        response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    client.headers["x-agent-legion-request"] = "1"
    return client
