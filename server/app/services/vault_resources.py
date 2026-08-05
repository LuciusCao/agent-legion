"""Resource-binding secret field masking and vault diversion (spec D13, VAULT-SECRET-001).

Secret config fields declared by resource provider schemas are diverted to
the vault on write (``apply_resources_patch``) and replaced by write-only
markers on read (``mask_resource_secrets``), so plaintext never crosses the
API boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from server.app.config_schema import ConfigSchemaError
from server.app.services.job_errors import InvalidOperationError
from server.app.services.vault import VaultError
from server.app.workflows.resource_schemas import validate_resource_bindings

if TYPE_CHECKING:
    from server.app.services.vault import VaultService


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
