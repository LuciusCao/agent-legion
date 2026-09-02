"""Route tests for the admin infra-connections surface (#335).

The production wiring lives in server/app/routes/__init__.py (integrated
centrally); these tests mount the router on private apps themselves so the
branch stays verifiable with or without that wiring.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from server.app.jobs import JobQueries
from server.app.routes.infra_connections import create_infra_connections_router
from tests.postgres_support import TEST_DATABASE_URL

CSRF = {"x-agent-legion-request": "1"}
INFRA_URL = "/api/admin/infra-connections"
TEST_URL = f"{INFRA_URL}/test"

_S3_ENV_KEYS = (
    "AGENT_LEGION_S3_BUCKET",
    "AGENT_LEGION_S3_ENDPOINT",
    "AGENT_LEGION_S3_REGION",
    "AGENT_LEGION_S3_ACCESS_KEY",
    "AGENT_LEGION_S3_SECRET_KEY",
    "AGENT_LEGION_S3_PUBLIC_ENDPOINT",
    "AGENT_LEGION_S3_ACCESS_KEY_FILE",
    "AGENT_LEGION_S3_SECRET_KEY_FILE",
)


def _mount_infra_connections(app) -> None:
    """Mount the router unless the central wiring already did (dedupe)."""
    if any(getattr(route, "path", "") == INFRA_URL for route in app.routes):
        return
    app.include_router(create_infra_connections_router(app.state.job_db), prefix="/api")


@pytest.fixture(autouse=True)
def _hermetic_s3_env(monkeypatch) -> None:
    """No ambient S3 config: each test opts in via monkeypatch.setenv."""
    for key in _S3_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_requires_auth(client_factory) -> None:
    with client_factory(
        authenticated=False, fresh=True, configure=_mount_infra_connections
    ) as anon:
        assert anon.get(INFRA_URL).status_code == 401
        assert anon.post(TEST_URL, json={"target": "database"}, headers=CSRF).status_code == 401


def test_member_forbidden(client_factory) -> None:
    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        response = client.post(
            "/api/users",
            json={"username": "infra_member", "password": "pw1"},
            headers=CSRF,
        )
        assert response.status_code == 201, response.text
        member = client.__class__(client.app)
        response = member.post(
            "/api/auth/login", json={"username": "infra_member", "password": "pw1"}
        )
        assert response.status_code == 200, response.text
        member.headers["x-agent-legion-request"] = "1"

        assert member.get(INFRA_URL).status_code == 403
        assert member.post(TEST_URL, json={"target": "database"}).status_code == 403


def test_get_database_summary(client_factory) -> None:
    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        response = client.get(INFRA_URL)

    assert response.status_code == 200, response.text
    database = response.json()["database"]
    # Expectations derive from the actual test DSN — CI's
    # AGENT_LEGION_TEST_DATABASE_URL carries user+password while the local
    # derived DSN carries neither, so nothing here may be hard-coded.
    dsn = urlsplit(TEST_DATABASE_URL)
    user = dsn.username or ""
    password_set = bool(dsn.password)
    host = dsn.hostname or ""
    name = dsn.path.lstrip("/")
    assert database["engine"] == dsn.scheme
    assert database["host"] == host
    assert database["port"] == dsn.port
    assert database["name"] == name
    assert database["user"] == user
    assert database["password_set"] is password_set
    # Masked URL: userinfo follows the DSN (password masked as ***), the
    # ?options=-csearch_path=... query string is dropped.
    userinfo = f"{user}:***@" if password_set else (f"{user}@" if user else "")
    masked_host = f"[{host}]" if ":" in host else host
    expected_masked = f"{dsn.scheme}://{userinfo}{masked_host}"
    if dsn.port is not None:
        expected_masked += f":{dsn.port}"
    if name:
        expected_masked += f"/{name}"
    assert database["masked_url"] == expected_masked
    assert "options=" not in database["masked_url"]


def test_get_storage_unconfigured(client_factory) -> None:
    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        response = client.get(INFRA_URL)

    assert response.status_code == 200, response.text
    storage = response.json()["storage"]
    assert storage == {
        "configured": False,
        "backend": "",
        "endpoint_url": "",
        "public_endpoint_url": "",
        "bucket": "",
        "region": "",
        "credentials": "unconfigured",
        "reachable": False,
    }


def _inject_s3_env(monkeypatch, *, with_keys: bool = True) -> None:
    monkeypatch.setenv("AGENT_LEGION_S3_BUCKET", "infra-bucket")
    monkeypatch.setenv("AGENT_LEGION_S3_ENDPOINT", "http://rustfs:9000")
    monkeypatch.setenv("AGENT_LEGION_S3_PUBLIC_ENDPOINT", "http://localhost:9100")
    monkeypatch.setenv("AGENT_LEGION_S3_REGION", "cn-test-1")
    if with_keys:
        monkeypatch.setenv("AGENT_LEGION_S3_ACCESS_KEY", "AKID")
        monkeypatch.setenv("AGENT_LEGION_S3_SECRET_KEY", "S3CR3T")


def test_get_storage_env_injected_static_credentials(client_factory, monkeypatch) -> None:
    _inject_s3_env(monkeypatch)
    # Never touch the network: reachability verdicts are stubbed OK.
    monkeypatch.setattr(
        "server.app.storage.probe.probe_settings",
        lambda settings, timeout_seconds=2.0: None,
    )

    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        response = client.get(INFRA_URL)

    assert response.status_code == 200, response.text
    storage = response.json()["storage"]
    assert storage["configured"] is True
    assert storage["backend"] == "RustFS"
    assert storage["endpoint_url"] == "http://rustfs:9000"
    assert storage["public_endpoint_url"] == "http://localhost:9100"
    assert storage["bucket"] == "infra-bucket"
    assert storage["region"] == "cn-test-1"
    assert storage["credentials"] == "static"
    assert storage["reachable"] is True
    # Credentials never ride the API payload.
    assert "S3CR3T" not in response.text


def test_get_storage_env_injected_default_chain(client_factory, monkeypatch) -> None:
    _inject_s3_env(monkeypatch, with_keys=False)
    monkeypatch.setattr(
        "server.app.storage.probe.probe_settings",
        lambda settings, timeout_seconds=2.0: "EndpointConnectionError: down",
    )

    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        response = client.get(INFRA_URL)

    assert response.status_code == 200, response.text
    storage = response.json()["storage"]
    assert storage["credentials"] == "default-chain"
    assert storage["reachable"] is False


def test_post_database_probe_ok(client_factory) -> None:
    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        response = client.post(TEST_URL, json={"target": "database"})

    assert response.status_code == 200, response.text
    assert response.json() == {"target": "database", "ok": True, "reason": None}


def test_post_database_probe_failure_relays_reason(client_factory, monkeypatch) -> None:
    def _boom(self):
        raise RuntimeError("boom-db")

    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        monkeypatch.setattr(JobQueries, "read", _boom)
        response = client.post(TEST_URL, json={"target": "database"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "target": "database",
        "ok": False,
        "reason": "RuntimeError: boom-db",
    }


def test_post_database_probe_failure_relays_first_line_only(client_factory, monkeypatch) -> None:
    """Driver messages put conninfo fragments on continuation lines; only the
    first line may ride the admin response (#335 review)."""
    dsn_with_password = "postgresql://legion:sup3rsecret@db.internal:5432/mydb"

    def _boom(self):
        raise RuntimeError(f"connection refused\nconninfo: {dsn_with_password}")

    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        monkeypatch.setattr(JobQueries, "read", _boom)
        response = client.post(TEST_URL, json={"target": "database"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "target": "database",
        "ok": False,
        "reason": "RuntimeError: connection refused",
    }
    assert "sup3rsecret" not in response.text
    assert dsn_with_password not in response.text


def test_post_storage_probe_failure_relays_reason(client_factory, monkeypatch) -> None:
    _inject_s3_env(monkeypatch)
    monkeypatch.setattr(
        "server.app.routes.infra_connections.probe_settings",
        lambda settings, timeout_seconds=2.0: "EndpointConnectionError: nope",
    )

    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        response = client.post(TEST_URL, json={"target": "storage"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "target": "storage",
        "ok": False,
        "reason": "EndpointConnectionError: nope",
    }


def test_post_storage_probe_unconfigured(client_factory) -> None:
    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        response = client.post(TEST_URL, json={"target": "storage"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is False
    assert "not configured" in payload["reason"]


def test_post_rejects_unknown_target(client_factory) -> None:
    with client_factory(fresh=True, configure=_mount_infra_connections) as client:
        response = client.post(TEST_URL, json={"target": "cache"})

    assert response.status_code == 422
