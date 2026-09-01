"""ConnectionTokenService: cache hit, expiry refresh, invalidation, injection."""

from __future__ import annotations

import base64
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server.app.jobs import JobQueries
from server.app.services import connection_adapters
from server.app.services.connection_adapters import (
    AcquiredToken,
    ConnectionAdapter,
    jwt_expires_at,
)
from server.app.services.connection_token_legacy import report_node_auth_failure
from server.app.services.connection_tokens import (
    ConnectionTokenService,
    inject_connection_config,
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
    connections = ConnectionService(job_db.dsn_identity, settings.config)
    tokens = ConnectionTokenService(job_db.dsn_identity, settings.config)
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
    """Legacy runtime shape: runtimes that still carry a ``job_db`` handle
    (legacy frozen node code calling the hook directly) resolve the DSN from
    that handle. Current nodes use the SDK marker channel instead, with the
    parent executor performing the invalidation (test_code_executor.py)."""
    connections, tokens = services
    connections.create("c1", "static_bearer", "", {"token": "tok-abc"})
    assert tokens.get_token("c1") == "tok-abc"
    assert connections.get("c1")["token"] is not None

    # Reproduce the legacy runtime shape (DB handle carried into node code).
    runtime: dict = {
        "node_config": {"connection": "c1"},
        "job_db": JobQueries(str(job_db.dsn_identity), Path(job_db.jobs_dir)),
    }

    report_node_auth_failure(runtime)

    # The cached token row is gone; the next get_token re-acquires.
    assert connections.get("c1")["token"] is None
    assert tokens.get_token("c1") == "tok-abc"


def test_report_node_auth_failure_job_db_path_fallback(services, job_db) -> None:
    """Runtimes that still carry the raw ``_job_db_path`` keep working."""
    connections, tokens = services
    connections.create("c1", "static_bearer", "", {"token": "tok-abc"})
    assert tokens.get_token("c1") == "tok-abc"

    report_node_auth_failure(
        {"node_config": {"connection": "c1"}, "_job_db_path": str(job_db.dsn_identity)}
    )

    assert connections.get("c1")["token"] is None


def test_report_node_auth_failure_silent_without_context(job_db) -> None:
    # No connection key or no DB handle: silent no-op.
    report_node_auth_failure({})
    report_node_auth_failure({"node_config": {"connection": "c1"}})
    report_node_auth_failure({"node_config": "not-a-mapping", "_job_db_path": "x"})
    report_node_auth_failure({"node_config": {"connection": "c1"}, "job_db": object()})
    report_node_auth_failure({"_job_db_path": str(job_db.dsn_identity)})
    # Reporting must never mask the original failure: an unreachable DB is
    # swallowed (logged), not raised.
    report_node_auth_failure(
        {
            "node_config": {"connection": "c1"},
            "_job_db_path": "postgresql://127.0.0.1:1/unreachable",
        }
    )


def test_report_node_auth_failure_facade_passthrough_no_getattr_escape(monkeypatch, job_db) -> None:
    """#187 getattr-escape closure: a facade-shaped ``job_db`` must reach
    ConnectionTokenService as the object itself, never unwrapped through
    ``getattr(job_db, "path")``/``dsn_identity``."""
    from server.app.services import connection_token_legacy

    received: list[object] = []

    class FakeFacade:
        # The facade surface services may rely on; accessing ``path`` here
        # would prove the legacy hook still unwraps the facade.
        dsn_identity = str(job_db.dsn_identity)

        @property
        def path(self) -> str:  # pragma: no cover - must not be touched
            raise AssertionError("facade must pass through, not unwrap .path")

    class RecorderService:
        def __init__(self, connect_source, settings_config=None) -> None:
            received.append(connect_source)

        def report_auth_failure(self, key: str) -> None:
            return None

    monkeypatch.setattr(connection_token_legacy, "ConnectionTokenService", RecorderService)
    facade = FakeFacade()
    report_node_auth_failure({"node_config": {"connection": "c1"}, "job_db": facade})
    assert received == [facade]

    # Fallback branch keeps handing the bare DSN string through.
    received.clear()
    report_node_auth_failure(
        {"node_config": {"connection": "c1"}, "_job_db_path": str(job_db.dsn_identity)}
    )
    assert received == [str(job_db.dsn_identity)]


def test_get_token_single_flight_under_concurrency(services, monkeypatch) -> None:
    """Concurrent get_token calls on one connection trigger exactly one
    credential exchange; every caller gets the same token."""
    calls: list[str] = []
    release = threading.Event()

    def _authenticate(config: dict, secrets: dict) -> AcquiredToken:
        # Hold the exchange open until every caller is queued behind the row
        # lock, so a missing single-flight guard would show up as extra calls.
        calls.append("exchange")
        release.wait(timeout=10)
        return AcquiredToken(token="tok-shared", expires_at=None)

    monkeypatch.setitem(
        connection_adapters._REGISTRY,
        "blocking_refresh",
        ConnectionAdapter(
            type="blocking_refresh",
            description="test adapter blocking inside authenticate",
            required_config_keys=(),
            secret_keys=("token",),
            authenticate=_authenticate,
            probe=lambda config, secrets: "ok",
        ),
    )
    connections, tokens = services
    connections.create("c1", "blocking_refresh", "", {"token": "seed"})

    results: list[str] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(5)

    def _worker() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(tokens.get_token("c1"))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=10)  # all workers are now racing get_token
    deadline = time.time() + 10
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert results == ["tok-shared"] * 4
    assert calls == ["exchange"]


def test_get_token_stops_serving_when_disabled(services, monkeypatch) -> None:
    """A disabled connection must not serve the cached token on the fast path."""
    connections, tokens = services
    connections.create("c1", "static_bearer", "", {"token": "tok-abc"})
    assert tokens.get_token("c1") == "tok-abc"

    connections.update("c1", enabled=False)

    with pytest.raises(InvalidOperationError, match="已停用"):
        tokens.get_token("c1")


def test_update_waits_for_inflight_refresh_and_invalidates(services, monkeypatch) -> None:
    """Admin update must serialize with an in-flight token refresh.

    The refresh resolves the old config, then blocks inside authenticate
    while holding the connection gate; the admin update (new credentials)
    runs concurrently and must queue on the same gate. After both commit,
    the token the refresh cached must be gone — the next get_token exchanges
    with the NEW credentials instead of resurrecting the old-config token.
    """
    seen_secrets: list[str] = []

    def _authenticate(config: dict, secrets: dict) -> AcquiredToken:
        seen_secrets.append(str(secrets.get("token")))
        return AcquiredToken(token=f"tok-{len(seen_secrets)}", expires_at=None)

    refresh_started = threading.Event()
    release_exchange = threading.Event()

    def _slow_authenticate(config: dict, secrets: dict) -> AcquiredToken:
        refresh_started.set()
        release_exchange.wait(timeout=10)
        return _authenticate(config, secrets)

    monkeypatch.setitem(
        connection_adapters._REGISTRY,
        "gate_refresh",
        ConnectionAdapter(
            type="gate_refresh",
            description="test adapter exposing the secret used per exchange",
            required_config_keys=(),
            secret_keys=("token",),
            authenticate=_slow_authenticate,
            probe=lambda config, secrets: "ok",
        ),
    )
    connections, tokens = services
    connections.create("c1", "gate_refresh", "", {"token": "old-secret"})

    refreshed: list[str] = []
    errors: list[BaseException] = []

    def _refresh() -> None:
        try:
            refreshed.append(tokens.get_token("c1"))
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion
            errors.append(exc)

    thread = threading.Thread(target=_refresh)
    thread.start()
    assert refresh_started.wait(timeout=10)
    # The refresh has resolved the OLD config and is mid-exchange, holding
    # the gate; the admin swap must block on that gate until it commits.
    update_done = threading.Event()

    def _update() -> None:
        try:
            connections.update("c1", config={"token": "new-secret"})
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion
            errors.append(exc)
        update_done.set()

    updater = threading.Thread(target=_update)
    updater.start()
    time.sleep(0.3)
    assert not update_done.is_set(), "update committed while the refresh still held the gate"
    release_exchange.set()
    updater.join(timeout=10)
    thread.join(timeout=10)

    assert not errors
    assert refreshed == ["tok-1"]
    # The update committed after the refresh: its cached token was deleted
    # under the same gate, so the next read exchanges with the new secret.
    assert tokens.get_token("c1") == "tok-2"
    assert seen_secrets == ["old-secret", "new-secret"]
