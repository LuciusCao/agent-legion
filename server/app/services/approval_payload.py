"""Approval verdict payload resolution against the gate's config schema."""

from __future__ import annotations

from typing import Any

from server.app.config_schema import (
    ConfigSchemaError,
    config_schema_defaults,
    validate_config_values,
)
from server.app.services.job_errors import InvalidOperationError


def approval_payload_defaults(node: Any) -> dict[str, Any]:
    return config_schema_defaults(node.config_schema or {})


def approval_manual_input_fields(node: Any) -> list[str]:
    schema = node.config_schema or {}
    properties = schema.get("properties") or {}
    return [
        name
        for name in schema.get("required") or []
        if not isinstance(properties.get(name), dict) or "default" not in properties[name]
    ]


def resolve_approval_payload(node: Any, payload: dict[str, Any] | None) -> dict[str, Any]:
    values = {**approval_payload_defaults(node), **(payload or {})}
    try:
        return validate_config_values(node.config_schema or {}, values, path="approval payload")
    except ConfigSchemaError as exc:
        raise InvalidOperationError(str(exc)) from exc
