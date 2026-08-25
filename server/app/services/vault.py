"""Encrypted per-workspace secret vault (spec D12–D14, VAULT-SECRET-001).

Secrets are stored Fernet-encrypted in the ``workspace_secrets`` table. The
master key comes from ``AGENT_LEGION_VAULT_MASTER_KEY`` /
``AGENT_LEGION_VAULT_MASTER_KEY_FILE`` (mapped onto ``vault.master_key`` /
``vault.master_key_file`` in the application config). The server starts
without a key, but vault writes and ``secret_ref`` resolution raise
``VaultMasterKeyMissingError`` until one is configured.

Plaintext never crosses this module's boundary: ``VaultService.get`` returns
it only to in-memory consumers (CMS token resolution, log redaction), and API
responses carry names and metadata exclusively.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction

_MAX_NAME_LENGTH = 128


class VaultError(RuntimeError):
    """Base error for vault operations."""


class VaultMasterKeyMissingError(VaultError):
    """Raised when a vault operation needs a master key that is not configured."""


def _read_key_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def resolve_master_key(settings_config: dict[str, Any] | None = None) -> str | None:
    """Resolve the Fernet master key: env, env file, then mapped config."""
    key = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY", "").strip()
    if key:
        return key
    key_file = os.environ.get("AGENT_LEGION_VAULT_MASTER_KEY_FILE", "").strip()
    if key_file:
        return _read_key_file(key_file)
    if isinstance(settings_config, dict):
        vault = settings_config.get("vault")
        if isinstance(vault, dict):
            configured = str(vault.get("master_key") or "").strip()
            if configured:
                return configured
            configured_file = str(vault.get("master_key_file") or "").strip()
            if configured_file:
                return _read_key_file(configured_file)
    return None


def _fernet(settings_config: dict[str, Any] | None) -> Fernet:
    key = resolve_master_key(settings_config)
    if key is None:
        raise VaultMasterKeyMissingError(
            "Vault master key is not configured; set AGENT_LEGION_VAULT_MASTER_KEY "
            "or AGENT_LEGION_VAULT_MASTER_KEY_FILE"
        )
    try:
        return Fernet(key.encode("utf-8"))
    except ValueError as exc:
        raise VaultError("Vault master key is not a valid Fernet key") from exc


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class VaultService:
    """Fernet encryption plus workspace_secrets persistence in one boundary."""

    def __init__(
        self,
        database_dsn: DatabaseDsn,
        settings_config: dict[str, Any] | None = None,
        memo: dict[tuple[str, str], str | None] | None = None,
    ) -> None:
        self._dsn = database_dsn
        self._settings_config = settings_config
        # Optional caller-owned memo keyed (workspace_id, name): the workflow
        # worker passes a per-pass dict so one scheduling pass re-reads a
        # secret_ref only once no matter how many nodes claim it (issue #124).
        # Values stay memory-only, same as any resolved plaintext
        # (VAULT-SECRET-001); the memo's lifetime bounds staleness, and
        # set/delete through this instance drop the entry immediately.
        self._memo = memo

    def set(self, workspace_id: str, name: str, plaintext: str) -> dict[str, Any]:
        if not name or len(name) > _MAX_NAME_LENGTH:
            raise VaultError(f"invalid vault secret name {name!r}")
        if not plaintext:
            raise VaultError("vault secret value must be a non-empty string")
        ciphertext = (
            _fernet(self._settings_config).encrypt(plaintext.encode("utf-8")).decode("utf-8")
        )
        with write_transaction(self._dsn) as conn:
            conn.execute(
                """
                insert into workspace_secrets(workspace_id, name, ciphertext)
                values (%s, %s, %s)
                on conflict(workspace_id, name)
                do update set ciphertext=excluded.ciphertext, updated_at=current_timestamp
                """,
                (workspace_id, name, ciphertext),
            )
        if self._memo is not None:
            self._memo.pop((workspace_id, name), None)
        metadata = self.get_metadata(workspace_id, name)
        if metadata is None:  # pragma: no cover - the upsert above just wrote it
            raise VaultError(f"vault secret {name!r} was not persisted")
        return metadata

    def get(self, workspace_id: str, name: str) -> str | None:
        """Return the decrypted plaintext, or None when the entry is missing.

        Callers must keep the plaintext in memory only: never persist it and
        never include it in an API response.
        """
        if self._memo is not None and (workspace_id, name) in self._memo:
            return self._memo[(workspace_id, name)]
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select ciphertext from workspace_secrets where workspace_id=%s and name=%s",
                (workspace_id, name),
            ).fetchone()
        if row is None:
            if self._memo is not None:
                self._memo[(workspace_id, name)] = None
            return None
        try:
            plaintext = (
                _fernet(self._settings_config)
                .decrypt(str(row["ciphertext"]).encode("utf-8"))
                .decode("utf-8")
            )
        except InvalidToken as exc:
            raise VaultError(
                f"vault secret {name!r} cannot be decrypted with the configured master key"
            ) from exc
        if self._memo is not None:
            self._memo[(workspace_id, name)] = plaintext
        return plaintext

    def delete(self, workspace_id: str, name: str) -> None:
        with write_transaction(self._dsn) as conn:
            conn.execute(
                "delete from workspace_secrets where workspace_id=%s and name=%s",
                (workspace_id, name),
            )
        if self._memo is not None:
            self._memo.pop((workspace_id, name), None)

    def get_metadata(self, workspace_id: str, name: str) -> dict[str, Any] | None:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select name, created_at, updated_at from workspace_secrets"
                " where workspace_id=%s and name=%s",
                (workspace_id, name),
            ).fetchone()
        if row is None:
            return None
        return {
            "name": str(row["name"]),
            "created_at": _timestamp(row["created_at"]),
            "updated_at": _timestamp(row["updated_at"]),
        }

    def list(self, workspace_id: str) -> list[dict[str, Any]]:
        """Names and metadata only — never ciphertext or plaintext."""
        with read_connection(self._dsn) as conn:
            rows = conn.execute(
                "select name, created_at, updated_at from workspace_secrets"
                " where workspace_id=%s order by name",
                (workspace_id,),
            ).fetchall()
        return [
            {
                "name": str(row["name"]),
                "created_at": _timestamp(row["created_at"]),
                "updated_at": _timestamp(row["updated_at"]),
            }
            for row in rows
        ]

    def resolve_secret_refs(self, config: dict[str, Any], workspace_id: str) -> dict[str, Any]:
        """Return a copy with ``{"secret_ref": name}`` values replaced by plaintext.

        Plain string values pass through unchanged (spec D14 compatibility
        window: legacy bindings and batch payloads still carry plaintext
        tokens). Resolution is in-memory only.
        """
        resolved = dict(config)
        for key, value in config.items():
            if not (isinstance(value, dict) and "secret_ref" in value):
                continue
            name = str(value["secret_ref"])
            plaintext = self.get(workspace_id, name)
            if plaintext is None:
                raise VaultError(f"vault secret {name!r} not found for workspace {workspace_id!r}")
            resolved[key] = plaintext
        return resolved


def collect_vault_plaintexts(vault: VaultService, workspace_id: str) -> list[str]:
    """Decrypted vault values of a workspace, for log redaction only.

    Returns an empty list when the vault is unreadable (e.g. no master key):
    redaction must never break log reading.
    """
    try:
        values = []
        for entry in vault.list(workspace_id):
            plaintext = vault.get(workspace_id, str(entry["name"]))
            if plaintext:
                values.append(plaintext)
        return values
    except VaultError:
        return []
