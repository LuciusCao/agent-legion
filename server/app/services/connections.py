"""Instance-level external connections: CRUD, secret diversion, probing.

A connection is an admin-managed, cross-workspace auth integration (e.g. the
CMS). ``config_json`` persists non-sensitive fields only; values of the
adapter-declared ``secret_keys`` are diverted into the instance vault under
``conn:<key>:<field>`` and stored as ``{"secret_ref": name}`` markers
(VAULT-SECRET-001). API payloads mask them as ``{"secret_set": bool}``.

The connection *mechanism* is platform-owned; protocol semantics live in the
adapters (see :mod:`server.app.services.connection_adapters`).
"""

from __future__ import annotations

import builtins
import json
import re
from datetime import datetime
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.connection_adapters import (
    ConnectionAdapter,
    ConnectionAdapterError,
    get_adapter,
)
from server.app.services.instance_vault import InstanceVaultService
from server.app.services.job_errors import (
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)
from server.app.services.vault import VaultError, resolve_master_key

_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def connection_secret_name(connection_key: str, field: str) -> str:
    """Deterministic instance-vault name for a connection secret field."""
    return f"conn:{connection_key}:{field}"


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _secret_ref_fields(stored: dict[str, Any]) -> list[str]:
    """Vault ref names recorded in a stored config (adapter-independent)."""
    return [
        str(value["secret_ref"])
        for value in stored.values()
        if isinstance(value, dict) and "secret_ref" in value
    ]


class ConnectionService:
    def __init__(
        self, database_dsn: DatabaseDsn, settings_config: dict[str, Any] | None = None
    ) -> None:
        self._dsn = database_dsn
        self._settings_config = settings_config
        self._vault = InstanceVaultService(database_dsn, settings_config)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                "select key, type, display_name, config_json, enabled, created_at, updated_at"
                " from external_connections order by key"
            ).fetchall()
        return [self._view(row) for row in rows]

    def get(self, key: str) -> dict[str, Any]:
        return self._view(self._row(key))

    def create(
        self,
        key: str,
        type_name: str,
        display_name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        if not _KEY_PATTERN.match(key):
            raise InvalidOperationError(
                "connection key 必须是小写字母/数字/连字符（如 cms-internal）"
            )
        adapter = self._adapter(type_name)
        stored = self._validate_config(adapter, config)
        # Secret values need the vault before the row can reference them; fail
        # before writing anything when the master key is missing (M10).
        secret_values = {
            field: config[field]
            for field in adapter.secret_keys
            if isinstance(config.get(field), str) and config[field].strip()
        }
        if secret_values and resolve_master_key(self._settings_config) is None:
            raise InvalidOperationError(
                "vault master key 未配置（AGENT_LEGION_VAULT_MASTER_KEY），无法保存 secret 字段"
            )
        # Row first with secrets dropped: a conflicting key is rejected before
        # any vault write can touch the existing connection's credentials.
        try:
            with write_transaction(self._dsn) as conn:
                conn.execute(
                    "insert into external_connections(key, type, display_name, config_json)"
                    " values (%s, %s, %s, %s)",
                    (key, adapter.type, display_name, json.dumps(stored, ensure_ascii=False)),
                )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ConflictError(f"connection {key!r} 已存在") from exc
            raise
        try:
            for field, value in secret_values.items():
                self._vault.set(connection_secret_name(key, field), value)
            if secret_values:
                stored_with_refs = dict(stored)
                for field in secret_values:
                    stored_with_refs[field] = {"secret_ref": connection_secret_name(key, field)}
                with write_transaction(self._dsn) as conn:
                    conn.execute(
                        "update external_connections set config_json=%s where key=%s",
                        (json.dumps(stored_with_refs, ensure_ascii=False), key),
                    )
        except VaultError as exc:
            raise InvalidOperationError(f"vault 写入失败: {exc}") from exc
        self._republish_executor_schemas()
        return self.get(key)

    def update(
        self,
        key: str,
        *,
        display_name: str | None = None,
        config: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        row = self._row(key)
        adapter = self._adapter(str(row["type"]))
        stored: dict[str, Any] | None = None
        secret_writes: dict[str, str] = {}
        secret_deletes: list[str] = []
        if config is not None:
            current = self._decode_config(row)
            stored, secret_writes, secret_deletes = self._validate_update(
                adapter, key, config, current
            )
            if secret_writes and resolve_master_key(self._settings_config) is None:
                raise InvalidOperationError(
                    "vault master key 未配置（AGENT_LEGION_VAULT_MASTER_KEY），无法保存 secret 字段"
                )
        try:
            for field, value in secret_writes.items():
                self._vault.set(connection_secret_name(key, field), value)
            for field in secret_deletes:
                self._vault.delete(connection_secret_name(key, field))
        except VaultError as exc:
            raise InvalidOperationError(f"vault 写入失败: {exc}") from exc
        with write_transaction(self._dsn) as conn:
            if display_name is not None:
                conn.execute(
                    "update external_connections set display_name=%s, updated_at=current_timestamp"
                    " where key=%s",
                    (display_name, key),
                )
            if stored is not None:
                conn.execute(
                    "update external_connections set config_json=%s, updated_at=current_timestamp"
                    " where key=%s",
                    (json.dumps(stored, ensure_ascii=False), key),
                )
                # Reconfiguration invalidates the cached token.
                conn.execute("delete from connection_tokens where connection_key=%s", (key,))
            if enabled is not None:
                conn.execute(
                    "update external_connections set enabled=%s, updated_at=current_timestamp"
                    " where key=%s",
                    (1 if enabled else 0, key),
                )
                if not enabled:
                    # A disabled connection must stop serving tokens at once.
                    conn.execute("delete from connection_tokens where connection_key=%s", (key,))
        return self.get(key)

    def delete(self, key: str) -> None:
        row = self._row(key)
        # Secret fields are derived from the stored ref markers, not the
        # adapter: deletion must work even when the adapter fails to load.
        ref_names = _secret_ref_fields(self._decode_config(row))
        with write_transaction(self._dsn) as conn:
            conn.execute("delete from external_connections where key=%s", (key,))
        for name in ref_names:
            self._vault.delete(name)

    # ------------------------------------------------------------------
    # Probe & runtime resolution
    # ------------------------------------------------------------------

    def probe(self, key: str) -> dict[str, Any]:
        adapter, config, secrets = self._resolved(key)
        try:
            message = adapter.probe(config, secrets)
        except (ConnectionAdapterError, VaultError) as exc:
            raise InvalidOperationError(str(exc)) from exc
        return {"ok": True, "message": message}

    def resolve_public_config(self, key: str) -> dict[str, Any]:
        """Non-secret connection config for in-memory runtime injection."""
        _, config, _ = self._resolved(key)
        return config

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _adapter(self, type_name: str) -> ConnectionAdapter:
        try:
            return get_adapter(type_name)
        except ConnectionAdapterError as exc:
            raise InvalidOperationError(str(exc)) from exc

    def _row(self, key: str) -> Any:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select key, type, display_name, config_json, enabled, created_at, updated_at"
                " from external_connections where key=%s",
                (key,),
            ).fetchone()
        if row is None:
            raise NotFoundError(
                f"connection {key!r} 不存在（在 admin 全局设置 → 外部服务连接 中创建）"
            )
        return row

    def _decode_config(self, row: Any) -> dict[str, Any]:
        try:
            loaded = json.loads(str(row["config_json"] or "{}"))
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _resolved(self, key: str) -> tuple[ConnectionAdapter, dict[str, Any], dict[str, str]]:
        row = self._row(key)
        if not row["enabled"]:
            raise InvalidOperationError(f"connection {key!r} 已停用")
        adapter = self._adapter(str(row["type"]))
        stored = self._decode_config(row)
        resolved = self._vault.resolve_secret_refs(stored)
        config = {k: v for k, v in resolved.items() if k not in adapter.secret_keys}
        secrets = {k: str(resolved[k]) for k in adapter.secret_keys if k in resolved}
        return adapter, config, secrets

    def _validate_config(
        self, adapter: ConnectionAdapter, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate a create payload; returns the non-secret stored config."""
        if not isinstance(config, dict):
            raise InvalidOperationError("config 必须是 JSON 对象")
        stored: dict[str, Any] = {}
        for name, value in config.items():
            if name in adapter.secret_keys:
                if not isinstance(value, str) and not (
                    isinstance(value, dict) and "secret_set" in value
                ):
                    raise InvalidOperationError(f"secret 字段 {name!r} 必须是字符串")
                continue
            if isinstance(value, dict) and ("secret_ref" in value or "secret_set" in value):
                raise InvalidOperationError(f"字段 {name!r} 不是 secret 字段，不接受引用标记")
            stored[name] = value
        for field in adapter.required_config_keys:
            if not str(stored.get(field) or "").strip():
                raise InvalidOperationError(f"缺少必填配置项 {field!r}（type={adapter.type}）")
        return stored

    def _validate_update(
        self,
        adapter: ConnectionAdapter,
        key: str,
        config: dict[str, Any],
        current: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str], builtins.list[str]]:
        """Validate an update payload.

        Returns (stored config, secret writes, secret deletes). Secret field
        semantics mirror node config secrets: a non-empty string upserts the
        vault; ``{"secret_set": ...}`` / ``{"secret_ref": ...}`` echoes keep
        the stored value; an empty string clears it.

        ``builtins.list`` is spelled out because the class defines a ``list``
        method that shadows the builtin in class-scope annotations.
        """
        if not isinstance(config, dict):
            raise InvalidOperationError("config 必须是 JSON 对象")
        stored: dict[str, Any] = {}
        writes: dict[str, str] = {}
        deletes: list[str] = []
        for name, value in config.items():
            if name not in adapter.secret_keys:
                if isinstance(value, dict) and ("secret_ref" in value or "secret_set" in value):
                    raise InvalidOperationError(f"字段 {name!r} 不是 secret 字段，不接受引用标记")
                stored[name] = value
                continue
            if isinstance(value, str):
                if value.strip():
                    writes[name] = value
                    stored[name] = {"secret_ref": connection_secret_name(key, name)}
                else:
                    deletes.append(name)
            elif isinstance(value, dict) and ("secret_ref" in value or "secret_set" in value):
                if name in current:
                    stored[name] = current[name]
            else:
                raise InvalidOperationError(f"secret 字段 {name!r} 必须是字符串")
        for field in adapter.required_config_keys:
            if not str(stored.get(field) or "").strip():
                raise InvalidOperationError(f"缺少必填配置项 {field!r}（type={adapter.type}）")
        return stored, writes, deletes

    def _republish_executor_schemas(self) -> None:
        """Backfill the ``connection`` config_schema property on CMS capabilities.

        Deployments migrated at v34 without credentials skipped the executor
        re-publish; creating the connection later must unlock it, otherwise no
        node config can reference the connection.
        """
        from server.app.db.migrations.external_connections import (
            _republish_executor_schema,
        )

        with write_transaction(self._dsn) as conn:
            _republish_executor_schema(conn)

    def _view(self, row: Any) -> dict[str, Any]:
        try:
            adapter = self._adapter(str(row["type"]))
            secret_keys: tuple[str, ...] = adapter.secret_keys
        except InvalidOperationError:
            secret_keys = ()
        stored = self._decode_config(row)
        masked: dict[str, Any] = {}
        for name, value in stored.items():
            # Marker-shaped values stay masked even when the adapter is
            # unavailable: ref markers never leave the server either way.
            if name in secret_keys or (isinstance(value, dict) and "secret_ref" in value):
                masked[name] = {"secret_set": bool(value)}
            else:
                masked[name] = value
        with read_connection(self._dsn) as conn:
            token_row = conn.execute(
                "select expires_at, refreshed_at from connection_tokens where connection_key=%s",
                (row["key"],),
            ).fetchone()
        token = None
        if token_row is not None:
            token = {
                "expires_at": _timestamp(token_row["expires_at"]),
                "refreshed_at": _timestamp(token_row["refreshed_at"]),
            }
        return {
            "key": str(row["key"]),
            "type": str(row["type"]),
            "display_name": str(row["display_name"]),
            "config": masked,
            "enabled": bool(row["enabled"]),
            "created_at": _timestamp(row["created_at"]),
            "updated_at": _timestamp(row["updated_at"]),
            "token": token,
        }
