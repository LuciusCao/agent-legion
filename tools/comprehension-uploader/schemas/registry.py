"""Schema version registry for comprehension info validation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from schemas.v1 import ComprehensionDataV1

SUPPORTED_VERSIONS: dict[str, type[BaseModel]] = {
    "v1": ComprehensionDataV1,
}


class UnsupportedSchemaVersionError(ValueError):
    """Raised when a schema version has no registered validator."""


class SchemaValidationError(ValueError):
    """Raised when comprehension_data fails schema validation."""


def validate(version: str, data: Any) -> Any:
    """Validate ``data`` against the model registered for ``version``.

    Args:
        version: Schema version string (e.g. ``"v1"``).
        data: Raw comprehension_data dict/list to validate.

    Returns:
        The validated model instance.

    Raises:
        UnsupportedSchemaVersionError: If ``version`` is not supported.
        SchemaValidationError: If ``data`` does not match the schema.
    """
    model = SUPPORTED_VERSIONS.get(version)
    if model is None:
        supported = ", ".join(sorted(SUPPORTED_VERSIONS))
        raise UnsupportedSchemaVersionError(
            f"Unsupported schema version {version!r}; supported versions: {supported}"
        )
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise SchemaValidationError(
            f"Schema validation failed for version {version!r}: {exc}"
        ) from exc
