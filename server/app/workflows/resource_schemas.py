"""Typed parameter schema views for resource providers (spec D10/D11).

Declarations live in the ``resource_providers:`` config yaml section (loaded
by ``resource_providers.py``); this module keeps the derived views consumed by
the settings payload and the ``resolve_cms_resource`` chain.
"""

from __future__ import annotations

from typing import Any

from server.app.config_schema import ConfigSchemaError, validate_config_values
from server.app.workflows.resource_providers import RESOURCE_PROVIDER_SCHEMAS


def _url_param_keys(schema: dict[str, Any]) -> tuple[str, ...]:
    """URL query params declared by a schema (secrets and env excluded)."""
    return tuple(
        key
        for key, prop in schema.get("properties", {}).items()
        if key != "api_url" and key != "env" and not prop.get("secret")
    )


def resource_param_keys(resource_key: str) -> tuple[str, ...]:
    """URL query params declared by one resource's provider schema."""
    schema = RESOURCE_PROVIDER_SCHEMAS.get(resource_key)
    if schema is None:
        return ()
    return _url_param_keys(schema)


RESOURCE_PARAM_KEYS = tuple(
    dict.fromkeys(
        key for schema in RESOURCE_PROVIDER_SCHEMAS.values() for key in _url_param_keys(schema)
    )
)


def validate_resource_bindings(resources: Any) -> None:
    """Validate a settings-page ``resources`` patch against the provider schemas."""
    if not isinstance(resources, dict):
        raise ConfigSchemaError("resources must be a mapping")
    for key, binding in resources.items():
        schema = RESOURCE_PROVIDER_SCHEMAS.get(key)
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


def resource_schemas_payload(providers: dict[str, Any]) -> dict[str, Any]:
    """Settings-payload view: resource key → provider name + typed schema."""
    return {
        key: {
            "provider": str(providers[key]["provider"]),
            "schema": RESOURCE_PROVIDER_SCHEMAS[key],
        }
        for key in providers
        if key in RESOURCE_PROVIDER_SCHEMAS
    }
