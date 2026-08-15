"""Typed config-schema subset for capability configuration (spec D7–D9).

Agents declare a ``config_schema`` (JSON Schema object subset) describing their
non-secret tunable parameters. Values are resolved along the chain
schema defaults → workflow node ``config`` → workspace override → job snapshot,
and only whitelisted, non-secret keys may leave the server in a manifest.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_SCHEMA_TYPES = ("string", "integer", "number", "boolean")
_TOP_LEVEL_KEYS = frozenset({"type", "properties", "required"})
_PROPERTY_KEYS = frozenset(
    {"type", "description", "default", "enum", "minimum", "maximum", "secret", "secret_ref"}
)


class ConfigSchemaError(ValueError):
    """Raised when a config schema declaration or a config value is invalid."""


def _type_matches(expected: str, value: Any) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _check_value(path: str, prop: dict[str, Any], value: Any) -> None:
    expected = prop["type"]
    if not _type_matches(expected, value):
        raise ConfigSchemaError(f"{path} must be of type {expected}")
    enum = prop.get("enum")
    if enum is not None and value not in enum:
        raise ConfigSchemaError(f"{path} must be one of {enum!r}")
    if expected in ("integer", "number"):
        minimum = prop.get("minimum")
        maximum = prop.get("maximum")
        if minimum is not None and value < minimum:
            raise ConfigSchemaError(f"{path} must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise ConfigSchemaError(f"{path} must be <= {maximum}")


def _validate_property(path: str, prop: Any) -> None:
    if not isinstance(prop, dict):
        raise ConfigSchemaError(f"{path} must be a mapping")
    unknown = set(prop) - _PROPERTY_KEYS
    if unknown:
        raise ConfigSchemaError(f"{path} has unsupported keys: {sorted(unknown)}")
    if prop.get("type") not in _SCHEMA_TYPES:
        raise ConfigSchemaError(f"{path}.type must be one of {list(_SCHEMA_TYPES)}")
    for marker in ("secret", "secret_ref"):
        if marker in prop and not isinstance(prop[marker], bool):
            raise ConfigSchemaError(f"{path}.{marker} must be a boolean")
    enum = prop.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum:
            raise ConfigSchemaError(f"{path}.enum must be a non-empty list")
        for item in enum:
            if not _type_matches(prop["type"], item):
                raise ConfigSchemaError(f"{path}.enum items must match the declared type")
    for bound in ("minimum", "maximum"):
        if bound in prop:
            if prop["type"] not in ("integer", "number"):
                raise ConfigSchemaError(f"{path}.{bound} requires a numeric type")
            if not _type_matches("number", prop[bound]):
                raise ConfigSchemaError(f"{path}.{bound} must be a number")
    if "minimum" in prop and "maximum" in prop and prop["minimum"] > prop["maximum"]:
        raise ConfigSchemaError(f"{path}.minimum must not exceed maximum")
    if "default" in prop:
        _check_value(f"{path}.default", prop, prop["default"])


def validate_config_schema(schema: Any, *, path: str = "config_schema") -> None:
    """Fail fast unless ``schema`` is a well-formed object-type schema subset."""
    if not isinstance(schema, dict):
        raise ConfigSchemaError(f"{path} must be a mapping")
    if not schema:
        return
    unknown = set(schema) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigSchemaError(f"{path} has unsupported keys: {sorted(unknown)}")
    if schema.get("type", "object") != "object":
        raise ConfigSchemaError(f"{path}.type must be 'object'")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ConfigSchemaError(f"{path}.properties must be a mapping")
    for name, prop in properties.items():
        if not isinstance(name, str) or not name:
            raise ConfigSchemaError(f"{path}.properties keys must be non-empty strings")
        _validate_property(f"{path}.properties.{name}", prop)
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ConfigSchemaError(f"{path}.required must be a list of strings")
    for name in required:
        if name not in properties:
            raise ConfigSchemaError(f"{path}.required references unknown property {name!r}")


def _properties(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    return properties if isinstance(properties, dict) else {}


def config_schema_defaults(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        name: prop["default"]
        for name, prop in _properties(schema).items()
        if isinstance(prop, dict) and "default" in prop
    }


def validate_config_values(
    schema: dict[str, Any],
    values: Any,
    *,
    partial: bool = False,
    path: str = "config",
) -> dict[str, Any]:
    """Validate ``values`` against ``schema`` and return a cleaned copy.

    Unknown keys are rejected (whitelist). With ``partial=True`` missing
    required keys are tolerated — individual override layers are partial;
    validate the merged effective config with ``partial=False``.
    """
    if not isinstance(values, dict):
        raise ConfigSchemaError(f"{path} must be a mapping")
    properties = _properties(schema)
    unknown = set(values) - set(properties)
    if unknown:
        raise ConfigSchemaError(f"{path} has unknown keys: {sorted(unknown)}")
    cleaned = dict(values)
    for name, value in cleaned.items():
        _check_value(f"{path}.{name}", properties[name], value)
    if not partial:
        for name in schema.get("required", []) or []:
            if name not in cleaned:
                raise ConfigSchemaError(f"{path}.{name} is required")
    return cleaned


def manifest_safe_config(schema: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Keep only schema-whitelisted, non-secret keys (CONFIG-MANIFEST-001).

    Keys marked ``secret`` never leave the server. Keys marked ``secret_ref``
    carry a vault reference name rather than a secret value, so they are safe
    to include for the future credential-vault hook.
    """
    properties = _properties(schema)
    return {
        name: value
        for name, value in config.items()
        if name in properties
        and isinstance(properties[name], dict)
        and not properties[name].get("secret", False)
    }


# Settings sections node code may ever see (VAULT-SECRET-001). The legacy
# business sections (asr) retired with the business workflows, so the
# whitelist is currently empty: instance-level sections (vault/auth/
# database/agent_workers/...) carry secrets or machine-local values and must
# never enter a manifest, the sandbox stdin payload, or the database.
NODE_SETTINGS_CONFIG_SECTIONS: tuple[str, ...] = ()


def node_safe_settings_config(settings_config: Mapping[str, Any]) -> dict[str, Any]:
    """Whitelist-filter a settings config to the sections node code may read."""
    return {
        section: dict(settings_config[section])
        for section in NODE_SETTINGS_CONFIG_SECTIONS
        if isinstance(settings_config.get(section), Mapping)
    }
