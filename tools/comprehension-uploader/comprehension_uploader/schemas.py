"""Re-export the comprehension info schema registry for the uploader package."""

from __future__ import annotations

from schemas.registry import (
    SUPPORTED_VERSIONS,
    SchemaValidationError,
    UnsupportedSchemaVersionError,
    validate,
)

__all__ = [
    "SUPPORTED_VERSIONS",
    "SchemaValidationError",
    "UnsupportedSchemaVersionError",
    "validate",
]
