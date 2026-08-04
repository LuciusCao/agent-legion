"""Typed parameter schema views for resource providers (spec D10/D11).

Declarations live in the ``resource_providers:`` config yaml section (parsed
by ``resource_providers.py`` and injected via ``Settings.resource_providers``);
this module keeps the derived views consumed by the settings payload and the
``resolve_cms_resource`` chain. Every function takes the schema mapping
explicitly so importing this module has no configuration side effects.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from server.app.config_schema import ConfigSchemaError, validate_config_values


def _url_param_keys(schema: dict[str, Any]) -> tuple[str, ...]:
    """URL query params declared by a schema (secrets and env excluded)."""
    return tuple(
        key
        for key, prop in schema.get("properties", {}).items()
        if key != "api_url" and key != "env" and not prop.get("secret")
    )


def resource_param_keys(
    resource_key: str, schemas: Mapping[str, Any] | None = None
) -> tuple[str, ...]:
    """URL query params declared by one resource's provider schema."""
    schema = (schemas or {}).get(resource_key)
    if schema is None:
        return ()
    return _url_param_keys(schema)


def validate_resource_bindings(resources: Any, schemas: Mapping[str, Any] | None = None) -> None:
    """Validate a settings-page ``resources`` patch against the provider schemas."""
    if not isinstance(resources, dict):
        raise ConfigSchemaError("resources must be a mapping")
    schemas = schemas or {}
    for key, binding in resources.items():
        schema = schemas.get(key)
        if schema is None:
            raise ConfigSchemaError(f"unknown resource {key!r}")
        if not isinstance(binding, dict):
            raise ConfigSchemaError(f"resources.{key} must be a mapping")
        validate_config_values(
            schema,
            binding.get("config", {}),
            partial=True,
            path=f"resources.{key}.config",
        )


def resource_schemas_payload(
    providers: Mapping[str, Any], schemas: Mapping[str, Any]
) -> dict[str, Any]:
    """Settings-payload view: resource key → provider name + typed schema."""
    return {
        key: {
            "provider": str(providers[key]["provider"]),
            "schema": schemas[key],
        }
        for key in providers
        if key in schemas
    }
