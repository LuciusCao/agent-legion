"""Global runtime token store for external connections.

Acquired tokens live Fernet-encrypted in ``connection_tokens`` (VAULT-SECRET-001
extends to instance scope). ``get_token`` is the single read path: a valid
cached token is returned directly (a disabled connection serves nothing); an
expired/missing one is refreshed while holding a connection-scoped advisory
gate (NOT the external_connections row lock): a burst of concurrent callers
(e.g. 96 workers) triggers at most one credential exchange — for login-based
adapters this is what keeps rate limits and account lockouts away. Only
concurrent refreshers of the same connection queue; readers of a valid token
never touch the lock. ``ConnectionService.update``/``delete`` take the same
gate (see :mod:`connection_gate`), so an admin credential swap can never
interleave with an in-flight exchange. Unlike the retired row-lock design,
the gate blocks no row and does not block readers of ``external_connections``
— but each queued refresher still holds one pool connection while waiting
(the transaction checks the connection out before taking the gate), and the
exchange itself runs with the transaction open, so ~pool-size concurrent
refreshers of the SAME connection can still exhaust the pool (the old row
lock had the same arithmetic and additionally blocked every
``external_connections`` reader). If that burst ever materializes, add an
in-process per-key single-flight so extra callers wait on an event instead
of queueing on pool connections. Adapters must use bounded network timeouts
so a hung upstream cannot hold the gate indefinitely.

Call sites that receive an upstream auth failure (HTTP 401/403 or an in-band
auth error code) should report it via ``NodeContext.report_auth_failure``
(nodes, marker channel — see workspace_libs.node_sdk) or
:meth:`report_auth_failure` and retry once via :meth:`get_token`;
persistent failure surfaces as a technical node failure.

Note: DB rows render datetimes as ISO strings (string_dict_row), so expiry
checks parse ``expires_at`` from text. Legacy frozen-node hooks live in
:mod:`connection_token_legacy`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import InvalidToken

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.connection_adapters import ConnectionAdapterError
from server.app.services.connection_gate import lock_connection_gate
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
    """Token cache with single-flight refresh (module docstring); the DSN
    param also accepts the JobQueries facade (BOUNDARY-DATA-001, #187)."""

    def __init__(
        self, database_dsn: ConnectSource, settings_config: dict[str, Any] | None = None
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
            # ConnectionService.update/delete take the same gate, so a
            # concurrent admin reconfiguration can never interleave with the
            # resolve→exchange→write sequence (see connection_gate).
            lock_connection_gate(conn, key)
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
                "select t.token_ciphertext, t.expires_at"
                " from connection_tokens t join external_connections c on c.key = t.connection_key"
                " where t.connection_key=%s and c.enabled = 1",
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
        except InvalidToken:
            # #204: a token that no longer decrypts under the current master
            # key (key rotation across the undecryptable ciphertext) is
            # exactly the refresh path — warn and let get_token re-acquire.
            # VaultMasterKeyMissingError (a VaultError) deliberately does NOT
            # land here: without a master key the refresh path cannot work
            # either, so it propagates as the startup-adjacent misconfig it is.
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
