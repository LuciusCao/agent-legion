"""Instance-scope secret vault (instance_secrets table).

Same Fernet semantics as the per-workspace vault (see
:mod:`server.app.services.vault`) but not bound to any workspace: external
connection credentials shared across workspaces live here
(VAULT-SECRET-001). Plaintext never crosses this module's boundary — getters
return it only to in-memory consumers, and API responses carry ref markers.
"""

from __future__ import annotations

from typing import Any

from cryptography.fernet import InvalidToken

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.services.vault import _MAX_NAME_LENGTH, VaultError, _fernet


class InstanceVaultService:
    """Fernet encryption plus instance_secrets persistence in one boundary."""

    def __init__(
        self, database_dsn: DatabaseDsn, settings_config: dict[str, Any] | None = None
    ) -> None:
        self._dsn = database_dsn
        self._settings_config = settings_config

    def set(self, name: str, plaintext: str) -> None:
        if not name or len(name) > _MAX_NAME_LENGTH:
            raise VaultError(f"invalid instance secret name {name!r}")
        if not plaintext:
            raise VaultError("instance secret value must be a non-empty string")
        ciphertext = (
            _fernet(self._settings_config).encrypt(plaintext.encode("utf-8")).decode("utf-8")
        )
        with write_transaction(self._dsn) as conn:
            conn.execute(
                """
                insert into instance_secrets(name, ciphertext)
                values (%s, %s)
                on conflict(name)
                do update set ciphertext=excluded.ciphertext, updated_at=current_timestamp
                """,
                (name, ciphertext),
            )

    def get(self, name: str) -> str | None:
        """Return the decrypted plaintext, or None when the entry is missing.

        Callers must keep the plaintext in memory only: never persist it and
        never include it in an API response.
        """
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select ciphertext from instance_secrets where name=%s", (name,)
            ).fetchone()
        if row is None:
            return None
        try:
            return (
                _fernet(self._settings_config)
                .decrypt(str(row["ciphertext"]).encode("utf-8"))
                .decode("utf-8")
            )
        except InvalidToken as exc:
            raise VaultError(
                f"instance secret {name!r} cannot be decrypted with the configured master key"
            ) from exc

    def delete(self, name: str) -> None:
        with write_transaction(self._dsn) as conn:
            conn.execute("delete from instance_secrets where name=%s", (name,))

    def resolve_secret_refs(self, config: dict[str, Any]) -> dict[str, Any]:
        """Return a copy with ``{"secret_ref": name}`` values replaced by plaintext."""
        resolved = dict(config)
        for key, value in config.items():
            if not (isinstance(value, dict) and "secret_ref" in value):
                continue
            name = str(value["secret_ref"])
            plaintext = self.get(name)
            if plaintext is None:
                raise VaultError(f"instance secret {name!r} not found")
            resolved[key] = plaintext
        return resolved
