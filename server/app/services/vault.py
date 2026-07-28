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
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from server.app.config_schema import ConfigSchemaError
from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import read_connection, write_transaction
from server.app.jobs import JobQueries
from server.app.services.job_errors import InvalidOperationError, NotFoundError
from server.app.settings import Settings
from server.app.workflows.resource_schemas import validate_resource_bindings

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


def resource_secret_name(resource_key: str, field: str) -> str:
    """Deterministic vault name for a resource-binding secret field."""
    return f"resource:{resource_key}:{field}"


def secret_field_names(
    resource_key: str, schemas: Mapping[str, Any] | None = None
) -> tuple[str, ...]:
    """Config fields marked ``secret: true`` by the resource provider schema."""
    schema = (schemas or {}).get(resource_key) or {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    return tuple(
        name for name, prop in properties.items() if isinstance(prop, dict) and prop.get("secret")
    )


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class VaultService:
    """Fernet encryption plus workspace_secrets persistence in one boundary."""

    def __init__(
        self, database_dsn: DatabaseDsn, settings_config: dict[str, Any] | None = None
    ) -> None:
        self._dsn = database_dsn
        self._settings_config = settings_config

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
                values (?, ?, ?)
                on conflict(workspace_id, name)
                do update set ciphertext=excluded.ciphertext, updated_at=current_timestamp
                """,
                (workspace_id, name, ciphertext),
            )
        metadata = self.get_metadata(workspace_id, name)
        if metadata is None:  # pragma: no cover - the upsert above just wrote it
            raise VaultError(f"vault secret {name!r} was not persisted")
        return metadata

    def get(self, workspace_id: str, name: str) -> str | None:
        """Return the decrypted plaintext, or None when the entry is missing.

        Callers must keep the plaintext in memory only: never persist it and
        never include it in an API response.
        """
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select ciphertext from workspace_secrets where workspace_id=? and name=?",
                (workspace_id, name),
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
                f"vault secret {name!r} cannot be decrypted with the configured master key"
            ) from exc

    def delete(self, workspace_id: str, name: str) -> None:
        with write_transaction(self._dsn) as conn:
            conn.execute(
                "delete from workspace_secrets where workspace_id=? and name=?",
                (workspace_id, name),
            )

    def get_metadata(self, workspace_id: str, name: str) -> dict[str, Any] | None:
        with read_connection(self._dsn) as conn:
            row = conn.execute(
                "select name, created_at, updated_at from workspace_secrets"
                " where workspace_id=? and name=?",
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
                " where workspace_id=? order by name",
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


def strip_resource_secret_fields(resources: Any, schemas: Mapping[str, Any] | None = None) -> Any:
    """Copy of a resources patch with secret field values removed.

    The stripped view is what ``validate_resource_bindings`` checks, so the
    ``{"secret_ref": ...}`` marker shape never reaches the generic config
    schema validation.
    """
    if not isinstance(resources, dict):
        return resources
    stripped: dict[str, Any] = {}
    for resource_key, binding in resources.items():
        fields = secret_field_names(str(resource_key), schemas)
        if not fields or not isinstance(binding, dict):
            stripped[resource_key] = binding
            continue
        raw_config = binding.get("config")
        if not isinstance(raw_config, dict):
            stripped[resource_key] = binding
            continue
        config = {key: value for key, value in raw_config.items() if key not in fields}
        stripped[resource_key] = {**binding, "config": config}
    return stripped


def apply_resource_secret_fields(
    vault: VaultService,
    workspace_id: str,
    resources: Any,
    current_resources: Any,
    schemas: Mapping[str, Any] | None = None,
) -> Any:
    """Move secret field values into the vault and store ``secret_ref`` dicts.

    - non-empty string → vault upsert, config keeps ``{"secret_ref": name}``
    - empty string / null → vault entry deleted, field removed
    - ``{"secret_ref": ...}`` → kept as-is (already a reference)
    - ``{"secret_set": ...}`` → frontend echo of the write-only marker; the
      stored value is kept (or the field dropped when nothing is stored)
    - field absent → the stored value is inherited so saving other fields
      does not silently drop the secret
    """
    if not isinstance(resources, dict):
        return resources
    result: dict[str, Any] = {}
    for resource_key, raw_binding in resources.items():
        fields = secret_field_names(str(resource_key), schemas)
        if not fields or not isinstance(raw_binding, dict):
            result[resource_key] = raw_binding
            continue
        binding = dict(raw_binding)
        raw_config = binding.get("config")
        config = dict(raw_config) if isinstance(raw_config, dict) else {}
        raw_current = (
            current_resources.get(resource_key) if isinstance(current_resources, dict) else None
        )
        raw_current_config = raw_current.get("config") if isinstance(raw_current, dict) else None
        current_config = raw_current_config if isinstance(raw_current_config, dict) else {}
        for field in fields:
            if field not in config:
                if field in current_config:
                    config[field] = current_config[field]
                continue
            name = resource_secret_name(str(resource_key), field)
            value = config[field]
            if isinstance(value, str):
                if value.strip():
                    vault.set(workspace_id, name, value)
                    config[field] = {"secret_ref": name}
                else:
                    vault.delete(workspace_id, name)
                    config.pop(field)
            elif isinstance(value, dict) and "secret_ref" in value:
                pass
            elif isinstance(value, dict) and set(value) == {"secret_set"}:
                if field in current_config:
                    config[field] = current_config[field]
                else:
                    config.pop(field)
            else:
                raise ConfigSchemaError(f"resources.{resource_key}.config.{field} must be a string")
        binding["config"] = config
        result[resource_key] = binding
    return result


def apply_resources_patch(
    vault: VaultService,
    workspace_id: str,
    workspace: dict[str, Any],
    resources_patch: Any,
    schemas: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a resources patch and divert secret fields to the vault.

    Returns the ``{"resources": ...}`` mapping ready to persist. Secret
    fields are stripped before schema validation so the ``secret_ref`` marker
    shape never reaches the generic config validation (spec D13).
    """
    try:
        validate_resource_bindings(strip_resource_secret_fields(resources_patch, schemas), schemas)
        raw_resource_config = workspace.get("resource_config")
        current_resources = (
            raw_resource_config.get("resources") if isinstance(raw_resource_config, dict) else None
        )
        resources = apply_resource_secret_fields(
            vault, workspace_id, resources_patch, current_resources, schemas
        )
    except (ConfigSchemaError, VaultError) as exc:
        raise InvalidOperationError(str(exc)) from exc
    return {"resources": resources}


def _is_secret_set(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, dict) and "secret_ref" in value


def mask_resource_secrets(
    resources: dict[str, Any], schemas: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Replace secret binding values with a write-only ``secret_set`` marker.

    Secret values (legacy plaintext or ``secret_ref`` dicts) never leave the
    server in the settings payload (VAULT-SECRET-001); the frontend only
    learns whether a value is set and re-enters it to overwrite.
    """
    masked: dict[str, Any] = {}
    for resource_key, binding in resources.items():
        fields = secret_field_names(str(resource_key), schemas)
        if not fields or not isinstance(binding, dict):
            masked[resource_key] = binding
            continue
        raw_config = binding.get("config")
        if not isinstance(raw_config, dict):
            masked[resource_key] = binding
            continue
        config = dict(raw_config)
        for field in fields:
            config[field] = {"secret_set": _is_secret_set(config.get(field))}
        masked[resource_key] = {**binding, "config": config}
    return masked


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


class WorkspaceSecretsService:
    """Workspace-scoped vault facade behind the secrets API (spec D13)."""

    def __init__(self, job_db: JobQueries, settings: Settings) -> None:
        self._job_db = job_db
        self._vault = VaultService(job_db.path, settings.config)

    def _ensure_workspace(self, workspace_id: str) -> None:
        if self._job_db.get_workspace(workspace_id) is None:
            raise NotFoundError("Workspace not found")

    def list(self, workspace_id: str) -> list[dict[str, Any]]:
        self._ensure_workspace(workspace_id)
        return self._vault.list(workspace_id)

    def set(self, workspace_id: str, name: str, value: str) -> dict[str, Any]:
        self._ensure_workspace(workspace_id)
        try:
            return self._vault.set(workspace_id, name, value)
        except VaultError as exc:
            raise InvalidOperationError(str(exc)) from exc

    def delete(self, workspace_id: str, name: str) -> None:
        self._ensure_workspace(workspace_id)
        self._vault.delete(workspace_id, name)
