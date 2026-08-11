"""ConnectionTokenService: cache hit, expiry refresh, invalidation, injection."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from server.app.services import connection_adapters
from server.app.services.connection_adapters import (
    AcquiredToken,
    ConnectionAdapter,
    jwt_expires_at,
)
from server.app.services.connection_tokens import (
    ConnectionTokenService,
    inject_connection_config,
    report_node_auth_failure,
)
from server.app.services.connections import ConnectionService
from server.app.services.job_errors import InvalidOperationError, NotFoundError


@pytest.fixture
def vault_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AGENT_LEGION_VAULT_MASTER_KEY", key)
    monkeypatch.delenv("AGENT_LEGION_VAULT_MASTER_KEY_FILE", raising=False)
    return key


@pytest.fixture
def services(job_db, settings, vault_key):
    connections = ConnectionService(job_db.path, settings.config)
    tokens = ConnectionTokenService(job_db.path, settings.config)
    return connections, tokens


def _jwt(exp: int) -> str:
    def seg(payload: dict) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg({'exp': exp})}.sig"


def test_jwt_expires_at_parses_exp() -> None:
    exp = int(time.time()) + 3600
    parsed = jwt_expires_at(_jwt(exp))
    assert parsed is not None and int(parsed.timestamp()) == exp
    assert jwt_expires_at("not-a-jwt") is None


def test_get_token_caches_static_token(services) -> None:
    connections, tokens = services
    connections.create("c1", "static_bearer", "", {"token": "tok-abc"})

    assert tokens.get_token("c1") == "tok-abc"
    assert tokens.get_token("c1") == "tok-abc"
    view = connections.get("c1")
    assert view["token"] is not None
    assert view["token"]["expires_at"] is None  # non-JWT: no known expiry


def _counting_adapter(calls: list[str], expires_at: datetime) -> ConnectionAdapter:
    """Adapter that counts authenticate calls and returns a fixed expiry."""

    def _authenticate(config: dict, secrets: dict) -> AcquiredToken:
        calls.append(f"tok-{len(calls) + 1}")
        return AcquiredToken(token=calls[-1], expires_at=expires_at)

    return ConnectionAdapter(
        type="counting_refresh",
        description="test adapter counting authenticate calls",
        required_config_keys=(),
        secret_keys=("token",),
        authenticate=_authenticate,
        probe=lambda config, secrets: "ok",
    )


def test_get_token_reauthenticates_after_expiry(services, monkeypatch) -> None:
    """An expired cached token must trigger a fresh credential exchange."""
    calls: list[str] = []
    monkeypatch.setitem(
        connection_adapters._REGISTRY,
        "counting_refresh",
        _counting_adapter(calls, datetime.now(UTC) - timedelta(seconds=1)),
    )
    connections, tokens = services
    connections.create("c1", "counting_refresh", "", {"token": "seed"})

    first = tokens.get_token("c1")
    second = tokens.get_token("c1")

    # The first acquisition cached an already-expired token, so the second
    # read refreshes instead of serving the stale cache entry.
    assert calls == ["tok-1", "tok-2"]
    assert first == "tok-1"
    assert second == "tok-2"


def test_get_token_keeps_valid_cached_token(services, monkeypatch) -> None:
    """A cached token well inside its validity window is served as-is."""
    calls: list[str] = []
    monkeypatch.setitem(
        connection_adapters._REGISTRY,
        "counting_refresh",
        _counting_adapter(calls, datetime.now(UTC) + timedelta(hours=1)),
    )
    connections, tokens = services
    connections.create("c1", "counting_refresh", "", {"token": "seed"})

    assert tokens.get_token("c1") == "tok-1"
    assert tokens.get_token("c1") == "tok-1"
    assert calls == ["tok-1"]


def test_get_token_jwt_expiry_round_trip(services) -> None:
    connections, tokens = services
    exp = int(time.time()) + 3600
    connections.create("c1", "static_bearer", "", {"token": _jwt(exp)})

    assert tokens.get_token("c1") == _jwt(exp)
    view = connections.get("c1")
    assert view["token"] is not None
    expires_at = datetime.fromisoformat(str(view["token"]["expires_at"]))
    assert int(expires_at.timestamp()) == exp


def test_report_auth_failure_invalidates(services) -> None:
    connections, tokens = services
    connections.create("c1", "static_bearer", "", {"token": "tok-abc"})
    tokens.get_token("c1")

    tokens.report_auth_failure("c1")

    view = connections.get("c1")
    assert view["token"] is None
    # Next read re-acquires (static_bearer: same token) and re-caches.
    assert tokens.get_token("c1") == "tok-abc"
    assert connections.get("c1")["token"] is not None


def test_get_token_unknown_connection(services) -> None:
    _, tokens = services
    with pytest.raises(NotFoundError):
        tokens.get_token("nope")


def test_get_token_disabled_connection(services) -> None:
    connections, tokens = services
    connections.create("c1", "static_bearer", "", {"token": "tok-abc"})
    connections.update("c1", enabled=False)
    with pytest.raises(InvalidOperationError, match="停用"):
        tokens.get_token("c1")


def test_inject_connection_config_from_node_key(services) -> None:
    connections, tokens = services
    connections.create("cms-internal", "static_bearer", "", {"base_url": "http://x", "token": "t"})

    injected = inject_connection_config({"connection": "cms-internal"}, {}, tokens)

    assert injected["connection_config"]["token"] == "t"
    assert injected["connection_config"]["base_url"] == "http://x"


def test_inject_connection_config_falls_back_to_schema_default(services) -> None:
    connections, tokens = services
    connections.create("cms-internal", "static_bearer", "", {"token": "t"})
    schema = {
        "type": "object",
        "properties": {"connection": {"type": "string", "default": "cms-internal"}},
    }

    injected = inject_connection_config({"bank_version": "v5"}, schema, tokens)

    assert injected["connection_config"]["token"] == "t"
    assert injected["bank_version"] == "v5"


def test_inject_connection_config_passthrough_without_key(services) -> None:
    _, tokens = services
    config = {"bank_version": "v5"}
    assert inject_connection_config(config, {}, tokens) is config


def test_inject_connection_config_legacy_token_passthrough(services) -> None:
    """Legacy frozen payloads carry a vault-resolved node ``token`` and no
    connection key: they are self-contained, so injection must pass them
    through untouched — even when the schema default names a connection that
    does not exist on this instance."""
    _, tokens = services
    schema = {
        "type": "object",
        "properties": {"connection": {"type": "string", "default": "missing"}},
    }
    config = {"token": "legacy-tok", "api_url": "https://cms.example.com/detail"}
    assert inject_connection_config(config, schema, tokens) is config


def test_report_node_auth_failure_invalidates_cached_token(services, job_db) -> None:
    connections, tokens = services
    connections.create("c1", "static_bearer", "", {"token": "tok-abc"})
    assert tokens.get_token("c1") == "tok-abc"
    assert connections.get("c1")["token"] is not None

    report_node_auth_failure(
        {"node_config": {"connection": "c1"}, "_job_db_path": str(job_db.path)}
    )

    # The cached token row is gone; the next get_token re-acquires.
    assert connections.get("c1")["token"] is None
    assert tokens.get_token("c1") == "tok-abc"


def test_report_node_auth_failure_silent_without_context(job_db) -> None:
    # No connection key or no DB handle: silent no-op.
    report_node_auth_failure({})
    report_node_auth_failure({"node_config": {"connection": "c1"}})
    report_node_auth_failure({"node_config": "not-a-mapping", "_job_db_path": "x"})
    report_node_auth_failure({"_job_db_path": str(job_db.path)})
    # Reporting must never mask the original failure: an unreachable DB is
    # swallowed (logged), not raised.
    report_node_auth_failure(
        {
            "node_config": {"connection": "c1"},
            "_job_db_path": "postgresql://127.0.0.1:1/unreachable",
        }
    )
