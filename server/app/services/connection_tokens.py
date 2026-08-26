"""Global runtime token store for external connections.

Acquired tokens live Fernet-encrypted in ``connection_tokens`` (VAULT-SECRET-001
extends to instance scope). ``get_token`` is the single read path: a valid
cached token is returned directly; an expired/missing one is refreshed while
holding a connection-scoped advisory lock (NOT the external_connections row
lock): a burst of concurrent callers (e.g. 96 workers) triggers at most one
credential exchange — for login-based adapters this is what keeps rate
limits and account lockouts away. Only concurrent refreshers of the same
connection queue; readers of a valid token never touch the lock. Unlike the
retired row-lock design, the advisory lock blocks no row and pins no
connection-pool peer (the pool is 32; 10-20s HTTP waits under a row lock
could starve it on a cold/expired connection). Adapters must use bounded
network timeouts so a hung upstream cannot hold the advisory lock
indefinitely.

Call sites that receive an upstream auth failure (HTTP 401/403 or an in-band
auth error code) should report it via ``NodeContext.report_auth_failure``
(nodes, marker channel — see workspace_libs.node_sdk) or
:meth:`report_auth_failure` and retry once via :meth:`get_token`;
persistent failure surfaces as a technical node failure.

Note: DB rows render datetimes as ISO strings (string_dict_row), so expiry
checks parse ``expires_at`` from text.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.connection_adapters import ConnectionAdapterError
from server.app.services.connections import ConnectionService
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.services.vault import _fernet

logger = logging.getLogger(__name__)

# Safety skew: treat tokens as expired this long before their stated expiry.
_EXPIRY_SKEW = timedelta(seconds=60)


def _parse_expiry(value: Any) -> datetime | None:
    """Normalize the DB-rendered expires_at (ISO string or datetime) to aware UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class ConnectionTokenService:
    def __init__(
        self, database_dsn: DatabaseDsn, settings_config: dict[str, Any] | None = None
    ) -> None:
        self._dsn = database_dsn
        self._connections = ConnectionService(database_dsn, settings_config)
        self._settings_config = settings_config

    def get_token(self, key: str) -> str:
        """Return a valid token, refreshing under a connection-scoped lock."""
        cached = self._read_valid(key)
        if cached is not None:
            return cached
        return self._refresh_token(key)

    def _refresh_token(self, key: str) -> str:
        """Single-flight refresh: one credential exchange per connection.

        The advisory lock serializes refreshers of the same connection; the
        exchange itself (network IO) runs with NO transaction and NO row
        locks held, so a slow upstream never pins pool connections. The
        cached-token check inside the lock makes queued losers of the race
        reuse the winner's token instead of exchanging again.
        """
        with write_transaction(self._dsn) as conn:
            # pg_advisory_xact_lock keys on the connection key text: scope is
            # per connection, unrelated keys never queue on each other.
            conn.execute(
                "select pg_advisory_xact_lock(hashtext('agent-legion-conn-token:' || %s))", (key,)
            )
            locked = conn.execute(
                "select key, enabled from external_connections where key=%s", (key,)
            ).fetchone()
            if locked is None:
                raise NotFoundError(f"connection {key!r} 不存在")
            if not locked["enabled"]:
                raise InvalidOperationError(f"connection {key!r} 已停用")
            row = conn.execute(
                "select token_ciphertext, expires_at from connection_tokens"
                " where connection_key=%s",
                (key,),
            ).fetchone()
            token = self._decrypt_if_valid(row)
            if token is not None:
                return token
            # Single-flight: hold the advisory lock ACROSS the exchange so
            # concurrent refreshers queue here instead of each performing
            # the credential exchange (login adapters are rate-limit and
            # lockout sensitive). No row locks are held and no other DB
            # work is blocked: pool peers see a normal open transaction.
            # Resolve config/secrets after taking the lock: resolving before
            # it would let a caller that queued behind a concurrent admin
            # update exchange the already-replaced credentials.
            adapter, config, secrets = self._connections._resolved(key)
            try:
                acquired = adapter.authenticate(config, secrets)
            except ConnectionAdapterError as exc:
                raise InvalidOperationError(f"connection {key!r} 获取 token 失败: {exc}") from exc
            ciphertext = (
                _fernet(self._settings_config)
                .encrypt(acquired.token.encode("utf-8"))
                .decode("utf-8")
            )
            conn.execute(
                """
                insert into connection_tokens(connection_key, token_ciphertext, expires_at)
                values (%s, %s, %s)
                on conflict(connection_key) do update set
                  token_ciphertext=excluded.token_ciphertext,
                  expires_at=excluded.expires_at,
                  refreshed_at=current_timestamp
                """,
                (key, ciphertext, acquired.expires_at),
            )
        logger.info("connection %s: token refreshed (expires_at=%s)", key, acquired.expires_at)
        return acquired.token

    def report_auth_failure(self, key: str) -> None:
        """Invalidate the cached token after an upstream auth failure."""
        logger.warning("connection %s: upstream auth failure, invalidating cached token", key)
        with write_transaction(self._dsn) as conn:
            conn.execute("delete from connection_tokens where connection_key=%s", (key,))

    def runtime_config(self, key: str) -> dict[str, Any]:
        """Non-secret connection config + a valid token, for in-memory injection."""
        config = self._connections.resolve_public_config(key)
        config["token"] = self.get_token(key)
        return config

    # ------------------------------------------------------------------

    def _read_valid(self, key: str) -> str | None:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select token_ciphertext, expires_at from connection_tokens"
                " where connection_key=%s",
                (key,),
            ).fetchone()
        return self._decrypt_if_valid(row)

    def _decrypt_if_valid(self, row: Any) -> str | None:
        if row is None:
            return None
        expires_at = _parse_expiry(row["expires_at"])
        if expires_at is not None and expires_at - _EXPIRY_SKEW <= datetime.now(UTC):
            return None
        try:
            return (
                _fernet(self._settings_config)
                .decrypt(str(row["token_ciphertext"]).encode("utf-8"))
                .decode("utf-8")
            )
        except Exception:
            logger.warning("cached connection token undecryptable, will refresh")
            return None


def inject_connection_config(
    node_config: dict[str, Any],
    config_schema: dict[str, Any],
    tokens: ConnectionTokenService,
) -> dict[str, Any]:
    """Attach the resolved external connection to a node's dispatch config.

    The connection key comes from the node config (``connection``) or the
    capability config_schema default — the latter covers frozen intake
    payloads created before the connection existed. A frozen payload carrying
    a legacy vault ``token`` keeps the legacy path instead: its credential is
    self-contained and the connection may not exist on this instance. The
    injected ``connection_config`` block (non-secret endpoint config + a
    plaintext token) is in-memory only: it is built after the frozen payload
    is read and never persisted; the dispatch layer only injects it for code
    executors, which do not go through the agent manifest
    (CONFIG-MANIFEST-001).
    """
    key = str(node_config.get("connection") or "").strip()
    if not key and node_config.get("token"):
        return node_config
    if not key:
        properties = config_schema.get("properties") if isinstance(config_schema, dict) else None
        prop = properties.get("connection") if isinstance(properties, dict) else None
        if isinstance(prop, dict):
            key = str(prop.get("default") or "").strip()
    if not key:
        return node_config
    injected = dict(node_config)
    injected["connection_config"] = tokens.runtime_config(key)
    return injected


def report_node_auth_failure(runtime: Mapping[str, Any]) -> None:
    """Legacy in-runtime hook: invalidate the connection's cached token.

    Superseded by the marker channel (``NodeContext.report_auth_failure`` in
    ``workspace_libs.node_sdk``): current nodes hold no database handle, so
    the parent executor performs the invalidation after the child exits. This
    function stays for legacy frozen node code that still calls it; it is a
    silent no-op when the runtime carries no connection or no DB handle.
    """
    node_config = runtime.get("node_config")
    key = (
        str(node_config.get("connection") or "").strip() if isinstance(node_config, Mapping) else ""
    )
    # The code executors pop ``_job_db_path`` before invoking node code and
    # hand the node a JobQueries handle instead (``runtime["job_db"]``, see
    # server/app/executors/code.py and _code_sandbox.py): resolve the DSN from
    # that handle first and keep ``_job_db_path`` only as a fallback for
    # runtimes that still carry the raw path.
    job_db = runtime.get("job_db")
    job_db_path = getattr(job_db, "path", None)
    dsn = str(job_db_path).strip() if job_db_path else ""
    if not dsn:
        dsn = str(runtime.get("_job_db_path") or "").strip()
    if not key or not dsn:
        return
    try:
        ConnectionTokenService(dsn).report_auth_failure(key)
    except Exception:  # reporting must never mask the original failure
        logger.exception("connection %s: failed to report auth failure", key)
