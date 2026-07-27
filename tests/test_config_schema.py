from __future__ import annotations

import pytest

from server.app.config_schema import (
    ConfigSchemaError,
    config_schema_defaults,
    manifest_safe_config,
    validate_config_schema,
    validate_config_values,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "subject_id": {"type": "string", "default": "math", "enum": ["math", "physics"]},
        "page_size": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
        "strict": {"type": "boolean"},
        "api_key": {"type": "string", "secret": True},
        "credential": {"type": "string", "secret_ref": True},
    },
    "required": ["strict"],
}


def test_validate_config_schema_accepts_empty_and_valid() -> None:
    validate_config_schema({})
    validate_config_schema(SCHEMA)


def test_validate_config_schema_rejects_non_object_top_level() -> None:
    with pytest.raises(ConfigSchemaError, match="must be 'object'"):
        validate_config_schema({"type": "array", "properties": {}})


def test_validate_config_schema_rejects_unknown_keys() -> None:
    with pytest.raises(ConfigSchemaError, match="unsupported keys"):
        validate_config_schema({"properties": {}, "additionalProperties": False})
    with pytest.raises(ConfigSchemaError, match="unsupported keys"):
        validate_config_schema({"properties": {"x": {"type": "string", "pattern": "^a"}}})


def test_validate_config_schema_rejects_bad_property() -> None:
    with pytest.raises(ConfigSchemaError, match="type must be one of"):
        validate_config_schema({"properties": {"x": {"type": "array"}}})
    with pytest.raises(ConfigSchemaError, match="enum must be a non-empty list"):
        validate_config_schema({"properties": {"x": {"type": "string", "enum": []}}})
    with pytest.raises(ConfigSchemaError, match="requires a numeric type"):
        validate_config_schema({"properties": {"x": {"type": "string", "minimum": 1}}})
    with pytest.raises(ConfigSchemaError, match="must not exceed maximum"):
        validate_config_schema(
            {"properties": {"x": {"type": "integer", "minimum": 5, "maximum": 1}}}
        )
    with pytest.raises(ConfigSchemaError, match="references unknown property"):
        validate_config_schema({"properties": {}, "required": ["missing"]})


def test_validate_config_schema_rejects_bad_default() -> None:
    with pytest.raises(ConfigSchemaError, match="default must be of type"):
        validate_config_schema({"properties": {"x": {"type": "integer", "default": "1"}}})
    with pytest.raises(ConfigSchemaError, match="default must be one of"):
        validate_config_schema(
            {"properties": {"x": {"type": "string", "enum": ["a"], "default": "b"}}}
        )


def test_config_schema_defaults_extracts_declared_defaults() -> None:
    assert config_schema_defaults(SCHEMA) == {"subject_id": "math", "page_size": 50}
    assert config_schema_defaults({}) == {}


def test_validate_config_values_accepts_and_cleans() -> None:
    cleaned = validate_config_values(SCHEMA, {"strict": True, "page_size": 20})
    assert cleaned == {"strict": True, "page_size": 20}


def test_validate_config_values_rejects_unknown_keys() -> None:
    with pytest.raises(ConfigSchemaError, match="unknown keys"):
        validate_config_values(SCHEMA, {"strict": True, "evil": "rm -rf"})


def test_validate_config_values_rejects_type_enum_and_bounds() -> None:
    with pytest.raises(ConfigSchemaError, match="page_size must be of type integer"):
        validate_config_values(SCHEMA, {"strict": True, "page_size": "20"})
    with pytest.raises(ConfigSchemaError, match="page_size must be of type integer"):
        validate_config_values(SCHEMA, {"strict": True, "page_size": True})
    with pytest.raises(ConfigSchemaError, match="subject_id must be one of"):
        validate_config_values(SCHEMA, {"strict": True, "subject_id": "history"})
    with pytest.raises(ConfigSchemaError, match="must be >= 1"):
        validate_config_values(SCHEMA, {"strict": True, "page_size": 0})
    with pytest.raises(ConfigSchemaError, match="must be <= 200"):
        validate_config_values(SCHEMA, {"strict": True, "page_size": 500})


def test_validate_config_values_enforces_required_unless_partial() -> None:
    with pytest.raises(ConfigSchemaError, match="strict is required"):
        validate_config_values(SCHEMA, {"page_size": 20})
    assert validate_config_values(SCHEMA, {"page_size": 20}, partial=True) == {"page_size": 20}


def test_validate_config_values_rejects_non_mapping() -> None:
    with pytest.raises(ConfigSchemaError, match="must be a mapping"):
        validate_config_values(SCHEMA, ["strict"])


def test_manifest_safe_config_drops_secret_and_unknown_keys() -> None:
    config = {
        "subject_id": "math",
        "api_key": "super-secret",
        "credential": "vault:cms-api-key",
        "undeclared": "x",
    }
    assert manifest_safe_config(SCHEMA, config) == {
        "subject_id": "math",
        "credential": "vault:cms-api-key",
    }
    assert manifest_safe_config({}, config) == {}
