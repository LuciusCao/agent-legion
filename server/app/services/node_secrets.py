"""Node-level secret fields: vault diversion, masking, and ref resolution.

A capability ``config_schema`` may mark properties ``secret: true``. Node
config values for those fields never persist in plaintext: the save chain
diverts them into the per-workspace vault under
``node:<workflow_key>:<node_key>:<field>`` and stores only
``{"secret_ref": name}`` markers (VAULT-SECRET-001). Frozen intake payloads
carry the same markers; the server resolves them to plaintext in memory at
dispatch time and in server-side consumers (question detail). API payloads only expose a write-only ``{"secret_set": bool}``
marker.
"""

from __future__ import annotations

from typing import Any

from server.app.config_schema import ConfigSchemaError
from server.app.services.vault import VaultService


def node_secret_name(workflow_key: str, node_key: str, field: str) -> str:
    """Deterministic vault name for a node config secret field."""
    return f"node:{workflow_key}:{node_key}:{field}"


def secret_config_fields(config_schema: dict[str, Any]) -> tuple[str, ...]:
    """Config fields marked ``secret: true`` by a capability config_schema."""
    properties = config_schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    return tuple(
        name for name, prop in properties.items() if isinstance(prop, dict) and prop.get("secret")
    )


def strip_secret_fields(config_schema: dict[str, Any], values: Any) -> Any:
    """Copy of a node config patch with secret field values removed.

    The stripped view is what generic schema validation checks, so the
    ``{"secret_ref": ...}`` / ``{"secret_set": ...}`` marker shapes never
    reach ``validate_config_values``.
    """
    if not isinstance(values, dict):
        return values
    fields = secret_config_fields(config_schema)
    if not fields:
        return values
    return {key: value for key, value in values.items() if key not in fields}


def apply_node_secret_fields(
    vault: VaultService,
    workspace_id: str,
    workflow_key: str,
    node_key: str,
    config_schema: dict[str, Any],
    values: dict[str, Any],
    current_values: dict[str, Any],
) -> dict[str, Any]:
    """Move secret field values into the vault and store ``secret_ref`` dicts.

    - non-empty string → vault upsert, config keeps ``{"secret_ref": name}``
    - empty string → vault entry deleted, field removed
    - ``{"secret_ref": ...}`` → kept as-is (already a reference)
    - ``{"secret_set": ...}`` → frontend echo of the write-only marker; the
      stored value is kept (or the field dropped when nothing is stored)
    - field absent → the stored value is inherited so saving other fields
      does not silently drop the secret
    - anything else (e.g. null) → ``ConfigSchemaError``
    """
    result = dict(values)
    for field in secret_config_fields(config_schema):
        if field not in result:
            if field in current_values:
                result[field] = current_values[field]
            continue
        name = node_secret_name(workflow_key, node_key, field)
        value = result[field]
        if isinstance(value, str):
            if value.strip():
                vault.set(workspace_id, name, value)
                result[field] = {"secret_ref": name}
            else:
                vault.delete(workspace_id, name)
                result.pop(field)
        elif isinstance(value, dict) and "secret_ref" in value:
            pass
        elif isinstance(value, dict) and set(value) == {"secret_set"}:
            if field in current_values:
                result[field] = current_values[field]
            else:
                result.pop(field)
        else:
            raise ConfigSchemaError(f"nodeConfig.{node_key}.{field} must be a string")
    return result


def mask_node_config_secrets(
    node_overrides: dict[str, Any],
    schemas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Replace secret node config values with a write-only ``secret_set`` marker.

    Secret values never leave the server in the settings payload
    (VAULT-SECRET-001); the frontend only learns whether a value is set and
    re-enters it to overwrite.
    """
    masked: dict[str, Any] = {}
    for node_key, raw_values in node_overrides.items():
        if not isinstance(raw_values, dict):
            masked[node_key] = raw_values
            continue
        fields = secret_config_fields(schemas.get(node_key) or {})
        if not fields:
            masked[node_key] = raw_values
            continue
        values = dict(raw_values)
        for field in fields:
            current = values.get(field)
            is_set = (
                bool(current.strip()) if isinstance(current, str) else isinstance(current, dict)
            ) and current is not None
            values[field] = {"secret_set": is_set}
        masked[node_key] = values
    return masked
